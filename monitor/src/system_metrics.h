#pragma once

#include <atomic>
#include <cstdint>
#include <memory>
#include <mutex>
#include <string>
#include <thread>

namespace localcodex {

struct SystemMetrics {
    double cpu_percent{-1.0};
    double gpu_percent{-1.0};
    std::int64_t ram_used_bytes{};
    std::int64_t ram_total_bytes{};
    std::int64_t vram_used_bytes{};
    std::int64_t vram_total_bytes{};
    std::string gpu_name;
    std::string gpu_source;
};

double utilization_percent(
    std::uint64_t previous_idle,
    std::uint64_t previous_total,
    std::uint64_t idle,
    std::uint64_t total
);

double memory_percent(std::int64_t used, std::int64_t total);

class SystemMetricsSampler {
public:
    SystemMetricsSampler();
    ~SystemMetricsSampler();
    SystemMetricsSampler(const SystemMetricsSampler&) = delete;
    SystemMetricsSampler& operator=(const SystemMetricsSampler&) = delete;

    SystemMetrics snapshot() const;
    void set_visible(bool visible);

private:
    struct Impl;
    void run();

    std::unique_ptr<Impl> impl_;
    mutable std::mutex mutex_;
    SystemMetrics metrics_;
    std::atomic_bool stop_{};
    std::atomic_bool visible_{true};
    std::thread worker_;
};

}  // namespace localcodex
