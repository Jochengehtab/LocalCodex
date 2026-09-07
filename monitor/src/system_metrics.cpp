#include "system_metrics.h"

#include <algorithm>
#include <chrono>
#include <filesystem>
#include <fstream>
#include <sstream>
#include <string_view>
#include <vector>

#ifdef _WIN32
#define NOMINMAX
#include <windows.h>
#include <dxgi1_4.h>
#include <pdh.h>
#include <pdhmsg.h>
#include <wrl/client.h>
#else
#include <dlfcn.h>
#endif

namespace localcodex {
namespace {
using namespace std::chrono_literals;

double clamp_percent(double value) {
    return std::clamp(value, 0.0, 100.0);
}

bool read_integer(const std::filesystem::path& path, std::int64_t& value) {
    std::ifstream input(path);
    return bool(input >> value);
}

using NvmlReturn = int;
using NvmlDevice = void*;
constexpr NvmlReturn nvml_success = 0;
struct NvmlUtilization { unsigned int gpu{}; unsigned int memory{}; };
struct NvmlMemory {
    unsigned long long total{};
    unsigned long long free{};
    unsigned long long used{};
};

struct NvmlApi {
#ifdef _WIN32
    HMODULE library{};
#else
    void* library{};
#endif
    NvmlReturn (*init)(){};
    NvmlReturn (*shutdown)(){};
    NvmlReturn (*get_count)(unsigned int*){};
    NvmlReturn (*get_handle)(unsigned int, NvmlDevice*){};
    NvmlReturn (*get_utilization)(NvmlDevice, NvmlUtilization*){};
    NvmlReturn (*get_memory)(NvmlDevice, NvmlMemory*){};
    NvmlReturn (*get_name)(NvmlDevice, char*, unsigned int){};
    bool initialized{};

    template <typename T>
    T symbol(const char* name) {
#ifdef _WIN32
        return reinterpret_cast<T>(GetProcAddress(library, name));
#else
        return reinterpret_cast<T>(dlsym(library, name));
#endif
    }

    NvmlApi() {
#ifdef _WIN32
        library = LoadLibraryW(L"nvml.dll");
#else
        library = dlopen("libnvidia-ml.so.1", RTLD_LAZY | RTLD_LOCAL);
#endif
        if (!library) return;
        init = symbol<decltype(init)>("nvmlInit_v2");
        shutdown = symbol<decltype(shutdown)>("nvmlShutdown");
        get_count = symbol<decltype(get_count)>("nvmlDeviceGetCount_v2");
        get_handle = symbol<decltype(get_handle)>("nvmlDeviceGetHandleByIndex_v2");
        get_utilization = symbol<decltype(get_utilization)>("nvmlDeviceGetUtilizationRates");
        get_memory = symbol<decltype(get_memory)>("nvmlDeviceGetMemoryInfo");
        get_name = symbol<decltype(get_name)>("nvmlDeviceGetName");
        initialized = init && shutdown && get_count && get_handle && get_utilization &&
            get_memory && init() == nvml_success;
    }

    ~NvmlApi() {
        if (initialized) shutdown();
#ifdef _WIN32
        if (library) FreeLibrary(library);
#else
        if (library) dlclose(library);
#endif
    }

    bool sample(SystemMetrics& result) {
        if (!initialized) return false;
        unsigned int count{};
        if (get_count(&count) != nvml_success || count == 0) return false;
        double maximum_utilization = -1.0;
        std::int64_t total_memory{};
        std::int64_t used_memory{};
        std::vector<std::string> names;
        for (unsigned int index = 0; index < count; ++index) {
            NvmlDevice device{};
            if (get_handle(index, &device) != nvml_success) continue;
            NvmlUtilization utilization{};
            if (get_utilization(device, &utilization) == nvml_success) {
                maximum_utilization = std::max(maximum_utilization, double(utilization.gpu));
            }
            NvmlMemory memory{};
            if (get_memory(device, &memory) == nvml_success) {
                total_memory += static_cast<std::int64_t>(memory.total);
                used_memory += static_cast<std::int64_t>(memory.used);
            }
            if (get_name) {
                char name[128]{};
                if (get_name(device, name, sizeof(name)) == nvml_success && name[0]) names.emplace_back(name);
            }
        }
        if (maximum_utilization < 0 && total_memory == 0) return false;
        result.gpu_percent = maximum_utilization;
        result.vram_total_bytes = total_memory;
        result.vram_used_bytes = used_memory;
        result.gpu_source = "NVML";
        if (!names.empty()) {
            result.gpu_name = names.front();
            if (names.size() > 1) result.gpu_name += " + " + std::to_string(names.size() - 1);
        }
        return true;
    }
};

#ifdef _WIN32

std::uint64_t filetime_value(const FILETIME& value) {
    ULARGE_INTEGER converted{};
    converted.LowPart = value.dwLowDateTime;
    converted.HighPart = value.dwHighDateTime;
    return converted.QuadPart;
}

std::string utf8(const wchar_t* value) {
    if (!value || !*value) return {};
    const int size = WideCharToMultiByte(CP_UTF8, 0, value, -1, nullptr, 0, nullptr, nullptr);
    if (size <= 1) return {};
    std::string result(static_cast<std::size_t>(size), '\0');
    WideCharToMultiByte(CP_UTF8, 0, value, -1, result.data(), size, nullptr, nullptr);
    result.pop_back();
    return result;
}

struct WindowsGpuFallback {
    PDH_HQUERY query{};
    PDH_HCOUNTER utilization{};
    PDH_HCOUNTER memory{};
    std::int64_t total_vram{};
    std::string names;
    bool ready{};

    WindowsGpuFallback() {
        Microsoft::WRL::ComPtr<IDXGIFactory1> factory;
        if (SUCCEEDED(CreateDXGIFactory1(IID_PPV_ARGS(&factory)))) {
            for (UINT index = 0;; ++index) {
                Microsoft::WRL::ComPtr<IDXGIAdapter1> adapter;
                if (factory->EnumAdapters1(index, &adapter) == DXGI_ERROR_NOT_FOUND) break;
                DXGI_ADAPTER_DESC1 description{};
                if (FAILED(adapter->GetDesc1(&description)) ||
                    (description.Flags & DXGI_ADAPTER_FLAG_SOFTWARE)) continue;
                total_vram += static_cast<std::int64_t>(description.DedicatedVideoMemory);
                if (!names.empty()) names += ", ";
                names += utf8(description.Description);
            }
        }
        if (PdhOpenQueryW(nullptr, 0, &query) != ERROR_SUCCESS) return;
        const auto util_status = PdhAddEnglishCounterW(
            query, L"\\GPU Engine(*)\\Utilization Percentage", 0, &utilization
        );
        const auto memory_status = PdhAddEnglishCounterW(
            query, L"\\GPU Adapter Memory(*)\\Dedicated Usage", 0, &memory
        );
        ready = util_status == ERROR_SUCCESS || memory_status == ERROR_SUCCESS;
        if (ready) PdhCollectQueryData(query);
    }

    ~WindowsGpuFallback() {
        if (query) PdhCloseQuery(query);
    }

    static std::vector<PDH_FMT_COUNTERVALUE_ITEM_W> values(PDH_HCOUNTER counter) {
        if (!counter) return {};
        DWORD bytes{};
        DWORD count{};
        if (PdhGetFormattedCounterArrayW(counter, PDH_FMT_DOUBLE, &bytes, &count, nullptr) != PDH_MORE_DATA) {
            return {};
        }
        std::vector<unsigned char> buffer(bytes);
        auto* items = reinterpret_cast<PDH_FMT_COUNTERVALUE_ITEM_W*>(buffer.data());
        if (PdhGetFormattedCounterArrayW(counter, PDH_FMT_DOUBLE, &bytes, &count, items) != ERROR_SUCCESS) {
            return {};
        }
        return {items, items + count};
    }

    bool sample(SystemMetrics& result) {
        if (!ready || PdhCollectQueryData(query) != ERROR_SUCCESS) return false;
        double maximum = -1.0;
        for (const auto& item : values(utilization)) {
            if (item.FmtValue.CStatus == ERROR_SUCCESS) {
                maximum = std::max(maximum, item.FmtValue.doubleValue);
            }
        }
        double used{};
        bool has_memory{};
        for (const auto& item : values(memory)) {
            if (item.FmtValue.CStatus == ERROR_SUCCESS) {
                used += std::max(0.0, item.FmtValue.doubleValue);
                has_memory = true;
            }
        }
        if (maximum < 0 && !has_memory && total_vram == 0) return false;
        result.gpu_percent = maximum < 0 ? -1.0 : clamp_percent(maximum);
        result.vram_used_bytes = static_cast<std::int64_t>(used);
        result.vram_total_bytes = total_vram;
        result.gpu_name = names;
        result.gpu_source = "PDH/DXGI";
        return true;
    }
};

#else

std::string linux_gpu_name(const std::filesystem::path& device) {
    std::ifstream input(device / "vendor");
    std::string vendor;
    input >> vendor;
    if (vendor == "0x1002") return "AMD GPU";
    if (vendor == "0x8086") return "Intel GPU";
    if (vendor == "0x10de") return "NVIDIA GPU";
    return "DRM GPU";
}

bool sample_linux_drm(SystemMetrics& result) {
    const std::filesystem::path root{"/sys/class/drm"};
    std::error_code error;
    if (!std::filesystem::exists(root, error)) return false;
    double maximum = -1.0;
    std::int64_t total{};
    std::int64_t used{};
    std::vector<std::string> names;
    for (const auto& entry : std::filesystem::directory_iterator(root, error)) {
        const auto filename = entry.path().filename().string();
        if (!filename.starts_with("card") || filename.find('-') != std::string::npos) continue;
        const auto device = entry.path() / "device";
        std::int64_t value{};
        if (read_integer(device / "gpu_busy_percent", value)) {
            maximum = std::max(maximum, double(value));
        }
        std::int64_t card_total{};
        std::int64_t card_used{};
        if (read_integer(device / "mem_info_vram_total", card_total)) total += card_total;
        if (read_integer(device / "mem_info_vram_used", card_used)) used += card_used;
        if (card_total > 0 || maximum >= 0) names.push_back(linux_gpu_name(device));
    }
    if (maximum < 0 && total == 0) return false;
    result.gpu_percent = maximum < 0 ? -1.0 : clamp_percent(maximum);
    result.vram_used_bytes = used;
    result.vram_total_bytes = total;
    result.gpu_source = "DRM sysfs";
    if (!names.empty()) {
        result.gpu_name = names.front();
        if (names.size() > 1) result.gpu_name += " + " + std::to_string(names.size() - 1);
    }
    return true;
}

#endif

}  // namespace

double utilization_percent(
    std::uint64_t previous_idle,
    std::uint64_t previous_total,
    std::uint64_t idle,
    std::uint64_t total
) {
    if (total <= previous_total || idle < previous_idle) return -1.0;
    const auto total_delta = total - previous_total;
    const auto idle_delta = idle - previous_idle;
    if (total_delta == 0 || idle_delta > total_delta) return -1.0;
    return clamp_percent(100.0 * double(total_delta - idle_delta) / double(total_delta));
}

double memory_percent(std::int64_t used, std::int64_t total) {
    if (used < 0 || total <= 0) return -1.0;
    return clamp_percent(100.0 * double(used) / double(total));
}

struct SystemMetricsSampler::Impl {
    NvmlApi nvml;
    std::uint64_t previous_idle{};
    std::uint64_t previous_total{};
    bool has_previous_cpu{};
#ifdef _WIN32
    WindowsGpuFallback gpu_fallback;
#endif

    SystemMetrics sample() {
        SystemMetrics result;
#ifdef _WIN32
        FILETIME idle{}, kernel{}, user{};
        if (GetSystemTimes(&idle, &kernel, &user)) {
            const auto idle_value = filetime_value(idle);
            const auto total_value = filetime_value(kernel) + filetime_value(user);
            if (has_previous_cpu) {
                result.cpu_percent = utilization_percent(
                    previous_idle, previous_total, idle_value, total_value
                );
            }
            previous_idle = idle_value;
            previous_total = total_value;
            has_previous_cpu = true;
        }
        MEMORYSTATUSEX memory{};
        memory.dwLength = sizeof(memory);
        if (GlobalMemoryStatusEx(&memory)) {
            result.ram_total_bytes = static_cast<std::int64_t>(memory.ullTotalPhys);
            result.ram_used_bytes = static_cast<std::int64_t>(memory.ullTotalPhys - memory.ullAvailPhys);
        }
        if (!nvml.sample(result)) gpu_fallback.sample(result);
#else
        std::ifstream cpu("/proc/stat");
        std::string label;
        std::uint64_t user{}, nice{}, system{}, idle{}, iowait{}, irq{}, softirq{}, steal{};
        if (cpu >> label >> user >> nice >> system >> idle >> iowait >> irq >> softirq >> steal) {
            const auto idle_value = idle + iowait;
            const auto total_value = user + nice + system + idle + iowait + irq + softirq + steal;
            if (has_previous_cpu) {
                result.cpu_percent = utilization_percent(
                    previous_idle, previous_total, idle_value, total_value
                );
            }
            previous_idle = idle_value;
            previous_total = total_value;
            has_previous_cpu = true;
        }
        std::ifstream memory("/proc/meminfo");
        std::string line;
        std::int64_t total_kib{};
        std::int64_t available_kib{};
        while (std::getline(memory, line)) {
            std::istringstream fields(line);
            std::string key;
            std::int64_t value{};
            fields >> key >> value;
            if (key == "MemTotal:") total_kib = value;
            else if (key == "MemAvailable:") available_kib = value;
        }
        result.ram_total_bytes = total_kib * 1024;
        result.ram_used_bytes = std::max<std::int64_t>(0, total_kib - available_kib) * 1024;
        if (!nvml.sample(result)) sample_linux_drm(result);
#endif
        return result;
    }
};

SystemMetricsSampler::SystemMetricsSampler()
    : impl_(std::make_unique<Impl>()), worker_(&SystemMetricsSampler::run, this) {}

SystemMetricsSampler::~SystemMetricsSampler() {
    stop_ = true;
    if (worker_.joinable()) worker_.join();
}

SystemMetrics SystemMetricsSampler::snapshot() const {
    std::scoped_lock guard(mutex_);
    return metrics_;
}

void SystemMetricsSampler::set_visible(bool visible) { visible_ = visible; }

void SystemMetricsSampler::run() {
    while (!stop_) {
        auto next = impl_->sample();
        {
            std::scoped_lock guard(mutex_);
            metrics_ = std::move(next);
        }
        const int delay_ms = visible_ ? 1000 : 5000;
        for (int elapsed = 0; elapsed < delay_ms && !stop_; elapsed += 50) {
            std::this_thread::sleep_for(50ms);
        }
    }
}

}  // namespace localcodex
