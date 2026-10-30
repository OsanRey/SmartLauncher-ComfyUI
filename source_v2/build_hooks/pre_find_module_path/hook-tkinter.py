"""Keep tkinter discoverable for Python Install Manager runtimes.

PyInstaller's stock pre-find hook treats these runtimes as broken because their
Tcl/Tk layout is not registered in the legacy location.  build_exe.py validates
and bundles that runtime explicitly, so clearing tkinter's search path here
would be incorrect.
"""


def pre_find_module_path(_hook_api) -> None:
    return
