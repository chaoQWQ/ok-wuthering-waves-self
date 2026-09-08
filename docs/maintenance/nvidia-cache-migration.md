# NVIDIA cache migration — 2026-09-08

This machine's NVIDIA DirectX shader cache was relocated from the system
drive to the local SATA SSD to free space for a Windows upgrade.

- Original application path: `C:\Users\zc\AppData\Local\NVIDIA\DXCache`
- Destination: `G:\NVIDIA_Cache\DXCache`
- Mechanism: NTFS directory junction at the original path.
- Dataset: 123 files, 27,967,082,496 bytes (about 26.05 GiB).

The initial copy was blocked on five files held by running applications and
Windows processes. After the user disabled shader caching and restarted,
the remaining files were copied and the junction was created. A write through
the original path was confirmed to arrive at the destination.

All 123 files passed SHA-256 comparison; one stale copied file was recopied
before passing verification. The old C-drive copy was then removed, releasing
26.05 GiB. C-drive free space measured 33.48 GiB after cleanup. The temporary
directory `DXCache.pre-move-20260908` no longer exists; the destination still
contains 123 files and the original path remains a junction to G.

After completion, restore NVIDIA Control Panel's **Shader Cache Size** to
**Driver Default**, the original setting. Keep drive G available under the
same drive letter. Check the junction again if a future driver update replaces
the cache directory. This migration covers DXCache only, not every NVIDIA cache.

No game files, process memory, or game input behavior were modified.
