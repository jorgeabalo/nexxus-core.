/* Read-only local metrics, APIs available on Mac OS X 10.6. No network/audio. */
#include <mach/mach.h>
#include <mach/mach_host.h>
#include <sys/sysctl.h>
#include <sys/mount.h>
#include <sys/time.h>
#include <unistd.h>
#include <stdint.h>
#include <stdio.h>
#include <time.h>
int main(void) {
    mach_port_t host = mach_host_self();
    host_cpu_load_info_data_t a, b;
    mach_msg_type_number_t n = HOST_CPU_LOAD_INFO_COUNT;
    vm_statistics_data_t vm;
    vm_size_t page;
    uint64_t memory = 0, total = 0, active = 0;
    size_t len = sizeof(memory);
    struct statfs disk;
    struct timeval boot;
    int mib[2] = {CTL_KERN, KERN_BOOTTIME}, i;
    if (host_statistics(host, HOST_CPU_LOAD_INFO, (host_info_t)&a, &n) != KERN_SUCCESS) return 1;
    usleep(250000);
    n = HOST_CPU_LOAD_INFO_COUNT;
    if (host_statistics(host, HOST_CPU_LOAD_INFO, (host_info_t)&b, &n) != KERN_SUCCESS) return 1;
    for (i = 0; i < CPU_STATE_MAX; i++) {
        uint32_t delta = (uint32_t)(b.cpu_ticks[i] - a.cpu_ticks[i]);
        total += delta;
        if (i != CPU_STATE_IDLE) active += delta;
    }
    if (!total) return 1;
    n = HOST_VM_INFO_COUNT;
    if (host_statistics(host, HOST_VM_INFO, (host_info_t)&vm, &n) != KERN_SUCCESS ||
        host_page_size(host, &page) != KERN_SUCCESS ||
        sysctlbyname("hw.memsize", &memory, &len, NULL, 0) || statfs("/", &disk)) return 1;
    uint64_t free_memory = (uint64_t)vm.free_count * page;
    uint64_t occupied = memory > free_memory ? memory - free_memory : 0;
    len = sizeof(boot);
    if (sysctl(mib, 2, &boot, &len, NULL, 0)) return 1;
    long long uptime = (long long)time(NULL) - (long long)boot.tv_sec;
    mach_port_deallocate(mach_task_self(), host);
    printf("{\"cpu_percent\":%.1f,\"ram_total\":%llu,\"ram_occupied\":%llu,"
           "\"disk_total\":%llu,\"disk_free\":%llu,\"uptime_seconds\":%lld}\n",
           total ? 100.0 * active / total : 0.0,
           (unsigned long long)memory, (unsigned long long)occupied,
           (unsigned long long)disk.f_blocks * disk.f_bsize,
           (unsigned long long)disk.f_bavail * disk.f_bsize, uptime > 0 ? uptime : 0);
    return 0;
}
