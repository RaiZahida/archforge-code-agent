from app.models import Game

def test_game_scoring():
    g = Game("1")
    assert g.view_score() == 0
    g.add_score(10)
    assert g.view_score() == 10
    g.add_score(5)
    assert g.view_score() == 15
