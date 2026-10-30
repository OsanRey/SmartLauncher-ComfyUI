from __future__ import annotations


def center_on_screen(window) -> None:
    window.update_idletasks()
    width = max(window.winfo_width(), window.winfo_reqwidth())
    height = max(window.winfo_height(), window.winfo_reqheight())
    x, y = centered_position(
        0,
        0,
        window.winfo_screenwidth(),
        window.winfo_screenheight(),
        width,
        height,
    )
    window.geometry(f"+{x}+{y}")


def center_over_parent(window, parent) -> None:
    window.update_idletasks()
    parent.update_idletasks()
    width = max(window.winfo_width(), window.winfo_reqwidth())
    height = max(window.winfo_height(), window.winfo_reqheight())
    x, y = centered_position(
        parent.winfo_rootx(),
        parent.winfo_rooty(),
        parent.winfo_width(),
        parent.winfo_height(),
        width,
        height,
    )
    window.geometry(f"+{x}+{y}")
    window.lift(parent)


def centered_position(
    container_x: int,
    container_y: int,
    container_width: int,
    container_height: int,
    width: int,
    height: int,
) -> tuple[int, int]:
    return (
        container_x + (container_width - width) // 2,
        container_y + (container_height - height) // 2,
    )
