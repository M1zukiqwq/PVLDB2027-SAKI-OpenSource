#include <rocksdb/db.h>
#include <rocksdb/listener.h>
#include <rocksdb/options.h>
#include <rocksdb/rate_limiter.h>
#include <rocksdb/slice.h>
#include <rocksdb/statistics.h>

#include <algorithm>
#include <array>
#include <atomic>
#include <chrono>
#include <cinttypes>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <memory>
#include <mutex>
#include <random>
#include <sstream>
#include <string>
#include <thread>
#include <unordered_map>
#include <vector>

namespace {

using Clock = std::chrono::steady_clock;

constexpr int kBuckets = 461;

uint64_t NowMicros() {
  return static_cast<uint64_t>(
      std::chrono::duration_cast<std::chrono::microseconds>(
          Clock::now().time_since_epoch())
          .count());
}

int BucketFor(uint64_t us) {
  if (us < 100) return static_cast<int>(us);
  if (us < 1000) return 100 + static_cast<int>((us - 100) / 10);
  if (us < 10000) return 190 + static_cast<int>((us - 1000) / 100);
  if (us < 100000) return 280 + static_cast<int>((us - 10000) / 1000);
  if (us < 1000000) return 370 + static_cast<int>((us - 100000) / 10000);
  return kBuckets - 1;
}

uint64_t BucketUpper(int bucket) {
  if (bucket < 100) return static_cast<uint64_t>(bucket);
  if (bucket < 190) return 100 + static_cast<uint64_t>(bucket - 99) * 10 - 1;
  if (bucket < 280) return 1000 + static_cast<uint64_t>(bucket - 189) * 100 - 1;
  if (bucket < 370) return 10000 + static_cast<uint64_t>(bucket - 279) * 1000 - 1;
  if (bucket < 460) return 100000 + static_cast<uint64_t>(bucket - 369) * 10000 - 1;
  return 2000000;
}

void AtomicMax(std::atomic<uint64_t>& target, uint64_t value) {
  uint64_t old = target.load(std::memory_order_relaxed);
  while (value > old &&
         !target.compare_exchange_weak(old, value, std::memory_order_relaxed)) {
  }
}

struct LatencyHist {
  std::array<std::atomic<uint64_t>, kBuckets> buckets;
  std::atomic<uint64_t> count{0};
  std::atomic<uint64_t> sum_us{0};
  std::atomic<uint64_t> max_us{0};

  LatencyHist() {
    for (auto& b : buckets) b.store(0, std::memory_order_relaxed);
  }

  void Add(uint64_t us) {
    buckets[BucketFor(us)].fetch_add(1, std::memory_order_relaxed);
    count.fetch_add(1, std::memory_order_relaxed);
    sum_us.fetch_add(us, std::memory_order_relaxed);
    AtomicMax(max_us, us);
  }
};

struct HistView {
  uint64_t count = 0;
  uint64_t sum_us = 0;
  uint64_t max_us = 0;
  std::array<uint64_t, kBuckets> buckets{};

  double Percentile(double pct) const {
    if (count == 0) return 0.0;
    uint64_t target = static_cast<uint64_t>(std::ceil((pct / 100.0) * count));
    target = std::max<uint64_t>(1, target);
    uint64_t seen = 0;
    for (int i = 0; i < kBuckets; ++i) {
      seen += buckets[i];
      if (seen >= target) return static_cast<double>(BucketUpper(i));
    }
    return static_cast<double>(max_us);
  }

  double Mean() const {
    return count ? static_cast<double>(sum_us) / static_cast<double>(count) : 0.0;
  }
};

HistView Snapshot(const LatencyHist& h) {
  HistView out;
  out.count = h.count.load(std::memory_order_relaxed);
  out.sum_us = h.sum_us.load(std::memory_order_relaxed);
  out.max_us = h.max_us.load(std::memory_order_relaxed);
  for (int i = 0; i < kBuckets; ++i) {
    out.buckets[i] = h.buckets[i].load(std::memory_order_relaxed);
  }
  return out;
}

struct WindowStats {
  LatencyHist write;
  LatencyHist read;
  std::atomic<uint64_t> write_bytes{0};
  std::atomic<uint64_t> read_bytes{0};
};

struct BgCounters {
  std::atomic<uint64_t> compact_input_bytes{0};
  std::atomic<uint64_t> compact_output_bytes{0};
  std::atomic<uint64_t> compact_cpu_us{0};
  std::atomic<uint64_t> compactions{0};
  std::atomic<uint64_t> flush_output_bytes{0};
  std::atomic<uint64_t> flushes{0};
};

class DebtListener : public rocksdb::EventListener {
 public:
  explicit DebtListener(BgCounters* counters) : counters_(counters) {}

  void OnCompactionCompleted(rocksdb::DB*, const rocksdb::CompactionJobInfo& ci) override {
    counters_->compact_input_bytes.fetch_add(ci.stats.total_input_bytes,
                                             std::memory_order_relaxed);
    counters_->compact_output_bytes.fetch_add(ci.stats.total_output_bytes,
                                              std::memory_order_relaxed);
    counters_->compact_cpu_us.fetch_add(ci.stats.cpu_micros,
                                        std::memory_order_relaxed);
    counters_->compactions.fetch_add(1, std::memory_order_relaxed);
  }

  void OnFlushCompleted(rocksdb::DB*, const rocksdb::FlushJobInfo& fi) override {
    counters_->flush_output_bytes.fetch_add(fi.table_properties.data_size,
                                            std::memory_order_relaxed);
    counters_->flushes.fetch_add(1, std::memory_order_relaxed);
  }

 private:
  BgCounters* counters_;
};

struct Args {
  std::string mode = "workload";
  std::string db;
  std::string tenant = "tenant0";
  std::string control_file;
  std::string metrics_file;
  int duration_sec = 120;
  int window_sec = 20;
  int threads = 2;
  int prefill_threads = 2;
  uint64_t num_keys = 80000;
  uint64_t prefill_keys = 80000;
  int value_size = 1024;
  uint64_t write_buffer_size = 2 * 1024 * 1024;
  int max_write_buffer_number = 3;
  int l0_compact_trigger = 2;
  int l0_slowdown_trigger = 6;
  int l0_stop_trigger = 10;
  uint64_t target_file_size_base = 2 * 1024 * 1024;
  int max_background_jobs = 2;
  int control_refresh_ms = 200;
  bool use_rocksdb_rate_limiter = true;
};

bool StartsWith(const std::string& s, const std::string& prefix) {
  return s.size() >= prefix.size() && s.compare(0, prefix.size(), prefix) == 0;
}

Args ParseArgs(int argc, char** argv) {
  Args args;
  for (int i = 1; i < argc; ++i) {
    std::string a(argv[i]);
    if (!StartsWith(a, "--")) continue;
    auto eq = a.find('=');
    std::string key = eq == std::string::npos ? a.substr(2) : a.substr(2, eq - 2);
    std::string val;
    if (eq == std::string::npos) {
      if (i + 1 < argc) val = argv[++i];
    } else {
      val = a.substr(eq + 1);
    }
    if (key == "mode") args.mode = val;
    else if (key == "db") args.db = val;
    else if (key == "tenant") args.tenant = val;
    else if (key == "control-file") args.control_file = val;
    else if (key == "metrics-file") args.metrics_file = val;
    else if (key == "duration-sec") args.duration_sec = std::stoi(val);
    else if (key == "window-sec") args.window_sec = std::stoi(val);
    else if (key == "threads") args.threads = std::stoi(val);
    else if (key == "prefill-threads") args.prefill_threads = std::stoi(val);
    else if (key == "num-keys") args.num_keys = std::stoull(val);
    else if (key == "prefill-keys") args.prefill_keys = std::stoull(val);
    else if (key == "value-size") args.value_size = std::stoi(val);
    else if (key == "write-buffer-size") args.write_buffer_size = std::stoull(val);
    else if (key == "max-write-buffer-number") args.max_write_buffer_number = std::stoi(val);
    else if (key == "l0-compact-trigger") args.l0_compact_trigger = std::stoi(val);
    else if (key == "l0-slowdown-trigger") args.l0_slowdown_trigger = std::stoi(val);
    else if (key == "l0-stop-trigger") args.l0_stop_trigger = std::stoi(val);
    else if (key == "target-file-size-base") args.target_file_size_base = std::stoull(val);
    else if (key == "max-background-jobs") args.max_background_jobs = std::stoi(val);
    else if (key == "control-refresh-ms") args.control_refresh_ms = std::stoi(val);
    else if (key == "use-rocksdb-rate-limiter") args.use_rocksdb_rate_limiter = std::stoi(val) != 0;
  }
  return args;
}

std::string KeyFor(uint64_t idx) {
  char buf[32];
  std::snprintf(buf, sizeof(buf), "key%016" PRIu64, idx);
  return std::string(buf);
}

std::string ValueFor(uint64_t seed, int value_size) {
  std::string v(static_cast<size_t>(value_size), 'x');
  uint64_t x = seed * 11400714819323198485ull + 0x9e3779b97f4a7c15ull;
  for (int i = 0; i < value_size; ++i) {
    x ^= x >> 12;
    x ^= x << 25;
    x ^= x >> 27;
    v[static_cast<size_t>(i)] = static_cast<char>('a' + (x % 26));
  }
  return v;
}

rocksdb::Options MakeOptions(const Args& args, BgCounters* counters,
                             std::shared_ptr<rocksdb::RateLimiter> rate_limiter = nullptr) {
  rocksdb::Options options;
  options.create_if_missing = true;
  options.compression = rocksdb::kNoCompression;
  options.write_buffer_size = args.write_buffer_size;
  options.max_write_buffer_number = args.max_write_buffer_number;
  options.level0_file_num_compaction_trigger = args.l0_compact_trigger;
  options.level0_slowdown_writes_trigger = args.l0_slowdown_trigger;
  options.level0_stop_writes_trigger = args.l0_stop_trigger;
  options.target_file_size_base = args.target_file_size_base;
  options.max_background_jobs = args.max_background_jobs;
  options.report_bg_io_stats = true;
  options.statistics = rocksdb::CreateDBStatistics();
  if (rate_limiter) {
    options.rate_limiter = std::move(rate_limiter);
  }
  if (counters != nullptr) {
    options.listeners.emplace_back(std::make_shared<DebtListener>(counters));
  }
  return options;
}

struct SharedControl {
  std::atomic<int64_t> write_qps_milli{0};
  std::atomic<int64_t> read_qps_milli{0};
  std::atomic<uint64_t> hot_start{0};
  std::atomic<uint64_t> hot_keys{1};
  std::atomic<int64_t> budget{0};
  std::atomic<int> stop{0};
  std::mutex mu;
  std::string true_tier = "mid";
  std::string assigned_tier = "mid";
};

std::unordered_map<std::string, std::string> ReadKvFile(const std::string& path) {
  std::unordered_map<std::string, std::string> out;
  std::ifstream in(path);
  std::string line;
  while (std::getline(in, line)) {
    auto pos = line.find('=');
    if (pos == std::string::npos) continue;
    out[line.substr(0, pos)] = line.substr(pos + 1);
  }
  return out;
}

void RefreshControl(const Args& args, SharedControl* ctl) {
  auto kv = ReadKvFile(args.control_file);
  auto get = [&](const std::string& key, const std::string& fallback) {
    auto it = kv.find(key);
    return it == kv.end() ? fallback : it->second;
  };
  ctl->write_qps_milli.store(static_cast<int64_t>(std::stod(get("write_qps", "0")) * 1000.0),
                             std::memory_order_relaxed);
  ctl->read_qps_milli.store(static_cast<int64_t>(std::stod(get("read_qps", "0")) * 1000.0),
                            std::memory_order_relaxed);
  ctl->hot_start.store(std::stoull(get("hot_start", "0")), std::memory_order_relaxed);
  ctl->hot_keys.store(std::max<uint64_t>(1, std::stoull(get("hot_keys", "1"))),
                      std::memory_order_relaxed);
  ctl->budget.store(std::stoll(get("budget", "0")), std::memory_order_relaxed);
  ctl->stop.store(std::stoi(get("stop", "0")), std::memory_order_relaxed);
  {
    std::lock_guard<std::mutex> lock(ctl->mu);
    ctl->true_tier = get("true_tier", ctl->true_tier);
    ctl->assigned_tier = get("assigned_tier", ctl->assigned_tier);
  }
}

uint64_t GetProperty(rocksdb::DB* db, const std::string& name) {
  uint64_t value = 0;
  if (!db->GetIntProperty(name, &value)) return 0;
  return value;
}

struct CounterSnapshot {
  uint64_t compact_input_bytes = 0;
  uint64_t compact_output_bytes = 0;
  uint64_t compact_cpu_us = 0;
  uint64_t compactions = 0;
  uint64_t flush_output_bytes = 0;
  uint64_t flushes = 0;
};

CounterSnapshot SnapshotCounters(const BgCounters& c) {
  CounterSnapshot s;
  s.compact_input_bytes = c.compact_input_bytes.load(std::memory_order_relaxed);
  s.compact_output_bytes = c.compact_output_bytes.load(std::memory_order_relaxed);
  s.compact_cpu_us = c.compact_cpu_us.load(std::memory_order_relaxed);
  s.compactions = c.compactions.load(std::memory_order_relaxed);
  s.flush_output_bytes = c.flush_output_bytes.load(std::memory_order_relaxed);
  s.flushes = c.flushes.load(std::memory_order_relaxed);
  return s;
}

uint64_t Delta(uint64_t now, uint64_t prev) {
  return now >= prev ? now - prev : 0;
}

int RunPrefill(const Args& args) {
  BgCounters counters;
  auto options = MakeOptions(args, &counters);
  rocksdb::DB* db = nullptr;
  auto status = rocksdb::DB::Open(options, args.db, &db);
  if (!status.ok()) {
    std::cerr << "open failed: " << status.ToString() << "\n";
    return 2;
  }
  std::unique_ptr<rocksdb::DB> holder(db);
  rocksdb::WriteOptions wopts;
  wopts.disableWAL = true;
  std::atomic<uint64_t> next{0};
  std::atomic<int> errors{0};
  std::vector<std::thread> threads;
  for (int t = 0; t < args.prefill_threads; ++t) {
    threads.emplace_back([&, t]() {
      while (true) {
        uint64_t idx = next.fetch_add(1, std::memory_order_relaxed);
        if (idx >= args.prefill_keys) break;
        auto s = db->Put(wopts, KeyFor(idx), ValueFor(idx + static_cast<uint64_t>(t), args.value_size));
        if (!s.ok()) errors.fetch_add(1, std::memory_order_relaxed);
      }
    });
  }
  for (auto& th : threads) th.join();
  rocksdb::FlushOptions fopts;
  fopts.wait = true;
  db->Flush(fopts).PermitUncheckedError();
  std::cout << "{\"mode\":\"prefill\",\"tenant\":\"" << args.tenant
            << "\",\"keys\":" << args.prefill_keys
            << ",\"errors\":" << errors.load() << "}\n";
  return errors.load() == 0 ? 0 : 3;
}

void WorkerLoop(int worker_id, const Args& args, rocksdb::DB* db, SharedControl* ctl,
                std::vector<std::unique_ptr<WindowStats>>* windows,
                std::atomic<uint64_t>* unique_bits, size_t unique_words,
                Clock::time_point start, std::atomic<int>* running) {
  rocksdb::WriteOptions wopts;
  wopts.disableWAL = true;
  rocksdb::ReadOptions ropts;
  std::mt19937_64 rng(static_cast<uint64_t>(worker_id + 1) * 0x9e3779b97f4a7c15ull);
  auto next_write = Clock::now();
  auto next_read = Clock::now();
  uint64_t op_seed = static_cast<uint64_t>(worker_id) << 48;
  const int total_windows = static_cast<int>(windows->size());

  while (running->load(std::memory_order_relaxed) && !ctl->stop.load(std::memory_order_relaxed)) {
    auto now = Clock::now();
    double write_qps = static_cast<double>(ctl->write_qps_milli.load(std::memory_order_relaxed)) / 1000.0;
    double read_qps = static_cast<double>(ctl->read_qps_milli.load(std::memory_order_relaxed)) / 1000.0;
    double write_per_thread = write_qps / std::max(1, args.threads);
    double read_per_thread = read_qps / std::max(1, args.threads);
    bool can_write = write_per_thread > 0.001;
    bool can_read = read_per_thread > 0.001;
    if (!can_write && !can_read) {
      std::this_thread::sleep_for(std::chrono::milliseconds(1));
      next_write = next_read = Clock::now();
      continue;
    }

    bool do_write = can_write && (!can_read || next_write <= next_read);
    auto due = do_write ? next_write : next_read;
    if (now < due) {
      std::this_thread::sleep_for(std::min(std::chrono::milliseconds(2),
                                           std::chrono::duration_cast<std::chrono::milliseconds>(due - now)));
      continue;
    }

    uint64_t hot_keys = std::max<uint64_t>(1, ctl->hot_keys.load(std::memory_order_relaxed));
    uint64_t hot_start = ctl->hot_start.load(std::memory_order_relaxed) % std::max<uint64_t>(1, args.num_keys);
    uint64_t key_idx = (hot_start + (rng() % hot_keys)) % std::max<uint64_t>(1, args.num_keys);
    if (unique_bits && unique_words > 0) {
      uint64_t wi = key_idx >> 6;
      if (wi < unique_words) {
        uint64_t b = 1ULL << (key_idx & 63);
        unique_bits[wi].fetch_or(b, std::memory_order_relaxed);
      }
    }
    uint64_t before = NowMicros();
    if (do_write) {
      auto s = db->Put(wopts, KeyFor(key_idx), ValueFor(++op_seed + key_idx, args.value_size));
      uint64_t after = NowMicros();
      int w = static_cast<int>(
          std::chrono::duration_cast<std::chrono::microseconds>(Clock::now() - start).count() /
          (static_cast<int64_t>(args.window_sec) * 1000000));
      if (w >= 0 && w < total_windows && s.ok()) {
        (*windows)[w]->write.Add(after - before);
        (*windows)[w]->write_bytes.fetch_add(static_cast<uint64_t>(args.value_size + 24),
                                             std::memory_order_relaxed);
      }
      auto interval = std::chrono::duration<double>(1.0 / write_per_thread);
      next_write += std::chrono::duration_cast<Clock::duration>(interval);
      if (next_write < Clock::now() - std::chrono::seconds(1)) next_write = Clock::now();
    } else {
      std::string value;
      auto s = db->Get(ropts, KeyFor(key_idx), &value);
      uint64_t after = NowMicros();
      int w = static_cast<int>(
          std::chrono::duration_cast<std::chrono::microseconds>(Clock::now() - start).count() /
          (static_cast<int64_t>(args.window_sec) * 1000000));
      if (w >= 0 && w < total_windows && (s.ok() || s.IsNotFound())) {
        (*windows)[w]->read.Add(after - before);
        (*windows)[w]->read_bytes.fetch_add(static_cast<uint64_t>(value.size()),
                                            std::memory_order_relaxed);
      }
      auto interval = std::chrono::duration<double>(1.0 / read_per_thread);
      next_read += std::chrono::duration_cast<Clock::duration>(interval);
      if (next_read < Clock::now() - std::chrono::seconds(1)) next_read = Clock::now();
    }
  }
}

int RunWorkload(const Args& args) {
  BgCounters counters;
  SharedControl ctl;
  RefreshControl(args, &ctl);
  std::shared_ptr<rocksdb::RateLimiter> rate_limiter;
  if (args.use_rocksdb_rate_limiter) {
    int64_t initial_budget = std::max<int64_t>(1, ctl.budget.load(std::memory_order_relaxed));
    rate_limiter.reset(rocksdb::NewGenericRateLimiter(
        initial_budget, 100 * 1000, 10, rocksdb::RateLimiter::Mode::kWritesOnly, false));
  }
  auto options = MakeOptions(args, &counters, rate_limiter);
  options.create_if_missing = false;
  rocksdb::DB* raw = nullptr;
  auto status = rocksdb::DB::Open(options, args.db, &raw);
  if (!status.ok()) {
    std::cerr << "open failed: " << status.ToString() << "\n";
    return 2;
  }
  std::unique_ptr<rocksdb::DB> db(raw);

  int window_count = (args.duration_sec + args.window_sec - 1) / args.window_sec;
  std::vector<std::unique_ptr<WindowStats>> windows;
  for (int i = 0; i < window_count + 1; ++i) {
    windows.emplace_back(std::make_unique<WindowStats>());
  }

  // Run-level distinct-key bitset over [0, args.num_keys). Each worker sets a
  // bit per accessed key index; cumulative popcount per window goes into the
  // metrics CSV as `unique_keys_touched_cumulative`. Memory cost = num_keys/8
  // bytes (e.g. 30 KiB for 240 000 keys).
  const size_t unique_words = static_cast<size_t>((args.num_keys + 63) / 64);
  std::unique_ptr<std::atomic<uint64_t>[]> unique_bits;
  if (unique_words > 0) {
    unique_bits = std::unique_ptr<std::atomic<uint64_t>[]>(new std::atomic<uint64_t>[unique_words]);
    for (size_t i = 0; i < unique_words; ++i) {
      unique_bits[i].store(0, std::memory_order_relaxed);
    }
  }

  std::ofstream metrics(args.metrics_file);
  metrics << "window,elapsed_start_sec,elapsed_end_sec,tenant,true_tier,assigned_tier,budget,"
          << "rate_limiter_bytes_per_sec,rate_limiter_bytes_delta,rate_limiter_requests_delta,"
          << "write_qps_target,read_qps_target,write_ops,read_ops,write_mean_us,write_p50_us,"
          << "write_p95_us,write_p99_us,write_p999_us,write_max_us,read_mean_us,read_p50_us,"
          << "read_p95_us,read_p99_us,read_p999_us,read_max_us,write_bytes,read_bytes,"
          << "compact_input_bytes,compact_output_bytes,flush_output_bytes,compactions,flushes,"
          << "compaction_cpu_us,pending_compaction_bytes,l0_files,cur_memtable_bytes,"
          << "delayed_write_rate,is_write_stopped,unique_keys_touched_cumulative\n";
  metrics.flush();

  std::atomic<int> running{1};
  auto start = Clock::now();
  std::thread control_thread([&]() {
    while (running.load(std::memory_order_relaxed)) {
      RefreshControl(args, &ctl);
      if (rate_limiter) {
        int64_t budget = std::max<int64_t>(1, ctl.budget.load(std::memory_order_relaxed));
        rate_limiter->SetBytesPerSecond(budget);
      }
      std::this_thread::sleep_for(std::chrono::milliseconds(args.control_refresh_ms));
    }
  });

  std::vector<std::thread> workers;
  for (int t = 0; t < args.threads; ++t) {
    workers.emplace_back(WorkerLoop, t, std::cref(args), db.get(), &ctl, &windows,
                         unique_bits.get(), unique_words, start, &running);
  }

  CounterSnapshot prev = SnapshotCounters(counters);
  int64_t prev_rl_bytes = rate_limiter ? rate_limiter->GetTotalBytesThrough() : 0;
  int64_t prev_rl_requests = rate_limiter ? rate_limiter->GetTotalRequests() : 0;
  for (int w = 0; w < window_count; ++w) {
    auto target = start + std::chrono::seconds((w + 1) * args.window_sec);
    std::this_thread::sleep_until(target);
    RefreshControl(args, &ctl);

    auto write = Snapshot(windows[w]->write);
    auto read = Snapshot(windows[w]->read);
    auto now = SnapshotCounters(counters);
    int64_t rl_rate = rate_limiter ? rate_limiter->GetBytesPerSecond() : 0;
    int64_t rl_bytes = rate_limiter ? rate_limiter->GetTotalBytesThrough() : 0;
    int64_t rl_requests = rate_limiter ? rate_limiter->GetTotalRequests() : 0;
    std::string true_tier;
    std::string assigned_tier;
    {
      std::lock_guard<std::mutex> lock(ctl.mu);
      true_tier = ctl.true_tier;
      assigned_tier = ctl.assigned_tier;
    }
    metrics << w << ','
            << (w * args.window_sec) << ','
            << ((w + 1) * args.window_sec) << ','
            << args.tenant << ','
            << true_tier << ','
            << assigned_tier << ','
            << ctl.budget.load(std::memory_order_relaxed) << ','
            << rl_rate << ','
            << std::max<int64_t>(0, rl_bytes - prev_rl_bytes) << ','
            << std::max<int64_t>(0, rl_requests - prev_rl_requests) << ','
            << std::fixed << std::setprecision(3)
            << (static_cast<double>(ctl.write_qps_milli.load(std::memory_order_relaxed)) / 1000.0) << ','
            << (static_cast<double>(ctl.read_qps_milli.load(std::memory_order_relaxed)) / 1000.0) << ','
            << write.count << ','
            << read.count << ','
            << write.Mean() << ','
            << write.Percentile(50.0) << ','
            << write.Percentile(95.0) << ','
            << write.Percentile(99.0) << ','
            << write.Percentile(99.9) << ','
            << write.max_us << ','
            << read.Mean() << ','
            << read.Percentile(50.0) << ','
            << read.Percentile(95.0) << ','
            << read.Percentile(99.0) << ','
            << read.Percentile(99.9) << ','
            << read.max_us << ','
            << windows[w]->write_bytes.load(std::memory_order_relaxed) << ','
            << windows[w]->read_bytes.load(std::memory_order_relaxed) << ','
            << Delta(now.compact_input_bytes, prev.compact_input_bytes) << ','
            << Delta(now.compact_output_bytes, prev.compact_output_bytes) << ','
            << Delta(now.flush_output_bytes, prev.flush_output_bytes) << ','
            << Delta(now.compactions, prev.compactions) << ','
            << Delta(now.flushes, prev.flushes) << ','
            << Delta(now.compact_cpu_us, prev.compact_cpu_us) << ','
            << GetProperty(db.get(), "rocksdb.estimate-pending-compaction-bytes") << ','
            << GetProperty(db.get(), "rocksdb.num-files-at-level0") << ','
            << GetProperty(db.get(), "rocksdb.cur-size-all-mem-tables") << ','
            << GetProperty(db.get(), "rocksdb.actual-delayed-write-rate") << ','
            << GetProperty(db.get(), "rocksdb.is-write-stopped") << ',';
    uint64_t cum_unique = 0;
    for (size_t i = 0; i < unique_words; ++i) {
      cum_unique += static_cast<uint64_t>(
          __builtin_popcountll(unique_bits[i].load(std::memory_order_relaxed)));
    }
    metrics << cum_unique << "\n";
    metrics.flush();
    prev = now;
    prev_rl_bytes = rl_bytes;
    prev_rl_requests = rl_requests;
  }

  running.store(0, std::memory_order_relaxed);
  for (auto& th : workers) th.join();
  if (control_thread.joinable()) control_thread.join();
  return 0;
}

}  // namespace

int main(int argc, char** argv) {
  Args args = ParseArgs(argc, argv);
  if (args.db.empty()) {
    std::cerr << "--db is required\n";
    return 1;
  }
  if (args.mode == "prefill") return RunPrefill(args);
  if (args.control_file.empty() || args.metrics_file.empty()) {
    std::cerr << "--control-file and --metrics-file are required in workload mode\n";
    return 1;
  }
  return RunWorkload(args);
}
