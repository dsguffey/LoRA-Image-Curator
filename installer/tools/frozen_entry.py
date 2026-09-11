import sys

if __name__ == '__main__':
    try:
        from install_manager.delivery_main import main
        result = main()
    except Exception as error:
        # Unattended acceptance must not hang on PyInstaller's unhandled-exception dialog.
        # Interactive startup errors are shown, but never fabricate success or continue.
        if not any(flag in sys.argv for flag in ('--accept', '--self-test', '--plan', '--ui-probe')):
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, str(error), 'LIC Install Manager — stopped', 0x10)
        result = 1
    raise SystemExit(result)
