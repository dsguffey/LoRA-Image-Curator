# New-Computer and Clean-Install QA

This is the final validation checkpoint before work begins on a bundled Python
runtime, executable, or installer. It distinguishes a genuinely new Windows
user from a fresh source folder on an established computer, where remembered
state under `%APPDATA%\LoRAImageCurator` can silently reopen an older catalog.

The automated check is read-only. It verifies signed release bytes and reports
the adjacent `venv` and per-user settings state; it never renames or deletes
files, settings, catalogs, models, or datasets.

## Phase 1: real new-computer / before setup

1. Download or clone v0.27.21 into a new folder on a Windows 11 computer that
   has never run LoRA Image Curator.
2. From PowerShell in that folder, run:

   ```powershell
   py -3 tools\clean_install_check.py --phase before-setup
   ```

3. The check must report no local `venv`, no existing per-user app data, and a
   valid signed release inventory.
4. Double-click `Setup and Launch LoRA Image Curator.bat`, choose first-time
   setup, select the appropriate official PyTorch path, and let base setup
   complete.

If this phase is intentionally rehearsed on an established computer, close the
app and temporarily rename `%APPDATA%\LoRAImageCurator` to a clearly marked
backup. Do not delete it. Restore the original name after the rehearsal.

## Phase 2: after guided setup

Run:

```powershell
venv\Scripts\python.exe tools\clean_install_check.py --phase after-setup
venv\Scripts\python.exe setup_assistant.py --check
```

Confirm the local environment exists, all required packages—including Recycle
Bin safety and exactly Transformers 4.56.2—are ready, and optional providers
are reported separately. Launch
the application from menu option 9 and verify that it starts without selecting
or reopening an old catalog.

Then check only the optional components available on that workstation:

- Face analysis: installer plus setup check.
- Body/pose analysis: `Install Body Analysis Dependencies.bat`, model download,
  and compatibility check.
- FFmpeg: PATH detection or a manually selected executable.

## Phase 3: established-user / upgrade behavior

After Phase 2 creates normal per-user settings, close the application, preserve
that settings directory and a synthetic/test catalog, then overlay the same or
next source release. Run:

```powershell
venv\Scripts\python.exe tools\clean_install_check.py --phase upgrade
```

Confirm the existing settings are detected, the app can reopen the remembered
test catalog, and catalogs, outputs, models, image sources, caches, and `venv`
remain unchanged. Never use a private or irreplaceable dataset as the only QA
copy.

## Release verdict

Record the Windows version, Python version, PyTorch/CUDA selection, GPU, and
which optional components were exercised. A headless or synthetic pass is
valuable preparation but does not substitute for this real new-computer test.
