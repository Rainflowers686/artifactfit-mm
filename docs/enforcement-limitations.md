# Enforcement limitations

ArtifactFit-MM 0.1 distinguishes enforcement from observation:

- wall time and bounded stdout/stderr writes are hard enforced by the runner;
- RAM, GPU memory, and workspace growth are polled and terminated after detection;
- Windows WDDM GPU process memory uses PDH when NVML does not expose per-process bytes;
- arbitrary child-process network bytes are not fully measurable in this version;
- network-deny environment flags are advisory, not a kernel firewall;
- workspace growth is a lower bound for an external program's download volume.

Receipts therefore use `DOWNLOAD_BYTES_UNMEASURED` and never imply a complete network
budget when an external program owns its downloader. Kernel-level network and cgroup
enforcement are out of scope for the vertical slice.
