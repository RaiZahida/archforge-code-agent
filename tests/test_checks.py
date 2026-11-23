from archforge.checks import import_check, reachable_modules


def make(tmp_path, files):
    for name, text in files.items():
        p = tmp_path / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)


def test_reachable_follows_imports_and_skips_orphans(tmp_path):
    make(tmp_path, {
        "main.py": "from app.ui.main_window import MainWindow\n",
        "app/__init__.py": "", "app/ui/__init__.py": "",
        "app/ui/main_window.py": "from app.engine import Game\nfrom . import menu\n",
        "app/ui/menu.py": "", "app/engine.py": "",
        "app/ui/help_view.py": "def view_help(): pass\n",   # orphan
    })
    live = {p.relative_to(tmp_path).as_posix() for p in reachable_modules(tmp_path)}
    assert {"main.py", "app/ui/main_window.py", "app/engine.py", "app/ui/menu.py"} <= live
    assert "app/ui/help_view.py" not in live


def test_import_check_finds_broken_import(tmp_path):
    make(tmp_path, {
        "main.py": "import app.views\n",
        "app/__init__.py": "",
        "app/models.py": "class Question: pass\n",
        "app/views.py": "from app.models import GameState\n",   # GameState was removed
    })
    bad = import_check(tmp_path)
    assert "app.views" in bad and "GameState" in bad["app.views"]


def test_import_check_clean_project(tmp_path):
    make(tmp_path, {"main.py": "import app.models\n", "app/__init__.py": "", "app/models.py": "X = 1\n"})
    assert import_check(tmp_path) == {}
