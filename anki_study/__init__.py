try:
    import aqt
except ImportError:
    pass  # unittest discovery outside Anki
else:
    from .dashboard import loaded, gui_hooks
