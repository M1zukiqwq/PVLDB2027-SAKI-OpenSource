#define _GNU_SOURCE

#include <dlfcn.h>
#include <errno.h>
#include <fcntl.h>
#include <pthread.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <limits.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/time.h>
#include <sys/uio.h>
#include <time.h>
#include <unistd.h>

#ifndef PATH_MAX
#define PATH_MAX 4096
#endif

static ssize_t (*real_write_fn)(int, const void *, size_t) = NULL;
static ssize_t (*real_pwrite_fn)(int, const void *, size_t, off_t) = NULL;
static ssize_t (*real_pwrite64_fn)(int, const void *, size_t, off64_t) = NULL;
static ssize_t (*real_writev_fn)(int, const struct iovec *, int) = NULL;
static ssize_t (*real_pwritev_fn)(int, const struct iovec *, int, off_t) = NULL;
static ssize_t (*real_pwritev64_fn)(int, const struct iovec *, int, off64_t) = NULL;

static pthread_once_t init_once = PTHREAD_ONCE_INIT;
static pthread_mutex_t throttle_mu = PTHREAD_MUTEX_INITIALIZER;
static __thread int in_hook = 0;

static char budget_path[PATH_MAX];
static char throttle_root[PATH_MAX];
static char throttle_log[PATH_MAX];
static char tenant_name[128];
static long refresh_ns = 200000000L;
static long log_ns = 1000000000L;

static double current_rate = 0.0;
static double tokens = 0.0;
static int64_t last_token_ns = 0;
static int64_t last_refresh_ns = 0;
static int64_t last_log_ns = 0;
static uint64_t total_bytes = 0;
static uint64_t total_calls = 0;
static uint64_t throttled_calls = 0;
static uint64_t total_sleep_ns = 0;

static int64_t now_ns(void) {
  struct timespec ts;
  clock_gettime(CLOCK_MONOTONIC, &ts);
  return ((int64_t)ts.tv_sec * 1000000000LL) + ts.tv_nsec;
}

static void sleep_ns(uint64_t ns) {
  struct timespec req;
  req.tv_sec = (time_t)(ns / 1000000000ULL);
  req.tv_nsec = (long)(ns % 1000000000ULL);
  while (nanosleep(&req, &req) != 0 && errno == EINTR) {
  }
}

static void init_symbols(void) {
  real_write_fn = (ssize_t(*)(int, const void *, size_t))dlsym(RTLD_NEXT, "write");
  real_pwrite_fn = (ssize_t(*)(int, const void *, size_t, off_t))dlsym(RTLD_NEXT, "pwrite");
  real_pwrite64_fn = (ssize_t(*)(int, const void *, size_t, off64_t))dlsym(RTLD_NEXT, "pwrite64");
  real_writev_fn = (ssize_t(*)(int, const struct iovec *, int))dlsym(RTLD_NEXT, "writev");
  real_pwritev_fn = (ssize_t(*)(int, const struct iovec *, int, off_t))dlsym(RTLD_NEXT, "pwritev");
  real_pwritev64_fn = (ssize_t(*)(int, const struct iovec *, int, off64_t))dlsym(RTLD_NEXT, "pwritev64");

  const char *p = getenv("THROTTLE_BUDGET_FILE");
  if (p) {
    snprintf(budget_path, sizeof(budget_path), "%s", p);
  }
  p = getenv("THROTTLE_ROOT");
  if (p) {
    char resolved[PATH_MAX];
    if (realpath(p, resolved) != NULL) {
      snprintf(throttle_root, sizeof(throttle_root), "%s", resolved);
    } else {
      snprintf(throttle_root, sizeof(throttle_root), "%s", p);
    }
  }
  p = getenv("THROTTLE_LOG");
  if (p) {
    snprintf(throttle_log, sizeof(throttle_log), "%s", p);
  }
  p = getenv("THROTTLE_TENANT");
  if (p) {
    snprintf(tenant_name, sizeof(tenant_name), "%s", p);
  } else {
    snprintf(tenant_name, sizeof(tenant_name), "unknown");
  }
  p = getenv("THROTTLE_REFRESH_MS");
  if (p && atol(p) > 0) {
    refresh_ns = atol(p) * 1000000L;
  }
  p = getenv("THROTTLE_LOG_MS");
  if (p && atol(p) > 0) {
    log_ns = atol(p) * 1000000L;
  }

  int64_t n = now_ns();
  last_token_ns = n;
  last_refresh_ns = 0;
  last_log_ns = n;
}

static void normalize_path(char *dst, size_t dst_size, const char *src) {
  if (dst_size == 0) {
    return;
  }
  dst[0] = '\0';
  if (!src || src[0] == '\0') {
    return;
  }
  char resolved[PATH_MAX];
  const char *chosen = src;
  if (realpath(src, resolved) != NULL) {
    chosen = resolved;
  }
  snprintf(dst, dst_size, "%s", chosen);
  char *deleted = strstr(dst, " (deleted)");
  if (deleted != NULL) {
    *deleted = '\0';
  }
  size_t len = strlen(dst);
  while (len > 1 && dst[len - 1] == '/') {
    dst[--len] = '\0';
  }
}

static int path_under_root(const char *path) {
  if (throttle_root[0] == '\0' || path == NULL || path[0] == '\0') {
    return 0;
  }
  size_t root_len = strlen(throttle_root);
  if (root_len == 1 && throttle_root[0] == '/') {
    return path[0] == '/';
  }
  if (strncmp(path, throttle_root, root_len) != 0) {
    return 0;
  }
  return path[root_len] == '\0' || path[root_len] == '/';
}

static void refresh_rate_locked(int64_t n) {
  if (budget_path[0] == '\0') {
    current_rate = 0.0;
    return;
  }
  if (n - last_refresh_ns < refresh_ns) {
    return;
  }
  last_refresh_ns = n;
  int fd = open(budget_path, O_RDONLY | O_CLOEXEC);
  if (fd < 0) {
    return;
  }
  char buf[128];
  ssize_t got = read(fd, buf, sizeof(buf) - 1);
  close(fd);
  if (got <= 0) {
    return;
  }
  buf[got] = '\0';
  char *end = NULL;
  unsigned long long value = strtoull(buf, &end, 10);
  current_rate = (double)value;
  if (tokens > current_rate * 2.0) {
    tokens = current_rate * 2.0;
  }
}

static int fd_under_root(int fd) {
  if (throttle_root[0] == '\0') {
    return 0;
  }
  struct stat st;
  if (fstat(fd, &st) != 0 || !S_ISREG(st.st_mode)) {
    return 0;
  }
  char proc_path[64];
  char path[2048];
  snprintf(proc_path, sizeof(proc_path), "/proc/self/fd/%d", fd);
  ssize_t len = readlink(proc_path, path, sizeof(path) - 1);
  if (len <= 0) {
    return 0;
  }
  path[len] = '\0';
  char normalized[PATH_MAX];
  normalize_path(normalized, sizeof(normalized), path);
  return path_under_root(normalized);
}

static void maybe_log_locked(int64_t n) {
  if (throttle_log[0] == '\0' || n - last_log_ns < log_ns || !real_write_fn) {
    return;
  }
  last_log_ns = n;
  char line[512];
  int len = snprintf(
      line, sizeof(line),
      "%lld,%s,%.0f,%llu,%llu,%llu,%llu\n",
      (long long)n, tenant_name, current_rate,
      (unsigned long long)total_bytes,
      (unsigned long long)total_calls,
      (unsigned long long)throttled_calls,
      (unsigned long long)total_sleep_ns);
  int fd = open(throttle_log, O_WRONLY | O_CREAT | O_APPEND | O_CLOEXEC, 0644);
  if (fd >= 0) {
    real_write_fn(fd, line, (size_t)len);
    close(fd);
  }
}

static void throttle_bytes(int fd, size_t count) {
  if (in_hook || count == 0) {
    return;
  }
  pthread_once(&init_once, init_symbols);
  if (!real_write_fn || !fd_under_root(fd)) {
    return;
  }

  uint64_t need_sleep = 0;
  in_hook = 1;
  pthread_mutex_lock(&throttle_mu);
  int64_t n = now_ns();
  refresh_rate_locked(n);
  if (current_rate > 0.0) {
    double elapsed = (double)(n - last_token_ns) / 1000000000.0;
    if (elapsed > 0.0) {
      tokens += elapsed * current_rate;
      double cap = current_rate * 2.0;
      if (tokens > cap) {
        tokens = cap;
      }
      last_token_ns = n;
    }
    total_calls++;
    total_bytes += (uint64_t)count;
    if (tokens >= (double)count) {
      tokens -= (double)count;
    } else {
      double deficit = (double)count - tokens;
      tokens = 0.0;
      need_sleep = (uint64_t)((deficit / current_rate) * 1000000000.0);
      if (need_sleep > 0) {
        throttled_calls++;
        total_sleep_ns += need_sleep;
      }
    }
  }
  maybe_log_locked(n);
  pthread_mutex_unlock(&throttle_mu);
  in_hook = 0;

  if (need_sleep > 0) {
    sleep_ns(need_sleep);
  }
}

static size_t iov_bytes(const struct iovec *iov, int iovcnt) {
  size_t total = 0;
  for (int i = 0; i < iovcnt; i++) {
    total += iov[i].iov_len;
  }
  return total;
}

ssize_t write(int fd, const void *buf, size_t count) {
  pthread_once(&init_once, init_symbols);
  if (!real_write_fn) {
    errno = EIO;
    return -1;
  }
  throttle_bytes(fd, count);
  return real_write_fn(fd, buf, count);
}

ssize_t pwrite(int fd, const void *buf, size_t count, off_t offset) {
  pthread_once(&init_once, init_symbols);
  if (!real_pwrite_fn) {
    errno = EIO;
    return -1;
  }
  throttle_bytes(fd, count);
  return real_pwrite_fn(fd, buf, count, offset);
}

ssize_t pwrite64(int fd, const void *buf, size_t count, off64_t offset) {
  pthread_once(&init_once, init_symbols);
  if (!real_pwrite64_fn) {
    errno = EIO;
    return -1;
  }
  throttle_bytes(fd, count);
  return real_pwrite64_fn(fd, buf, count, offset);
}

ssize_t writev(int fd, const struct iovec *iov, int iovcnt) {
  pthread_once(&init_once, init_symbols);
  if (!real_writev_fn) {
    errno = EIO;
    return -1;
  }
  throttle_bytes(fd, iov_bytes(iov, iovcnt));
  return real_writev_fn(fd, iov, iovcnt);
}

ssize_t pwritev(int fd, const struct iovec *iov, int iovcnt, off_t offset) {
  pthread_once(&init_once, init_symbols);
  if (!real_pwritev_fn) {
    errno = EIO;
    return -1;
  }
  throttle_bytes(fd, iov_bytes(iov, iovcnt));
  return real_pwritev_fn(fd, iov, iovcnt, offset);
}

ssize_t pwritev64(int fd, const struct iovec *iov, int iovcnt, off64_t offset) {
  pthread_once(&init_once, init_symbols);
  if (!real_pwritev64_fn) {
    errno = EIO;
    return -1;
  }
  throttle_bytes(fd, iov_bytes(iov, iovcnt));
  return real_pwritev64_fn(fd, iov, iovcnt, offset);
}
