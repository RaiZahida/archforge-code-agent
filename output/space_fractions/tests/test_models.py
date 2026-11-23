from app.models import Game, Question, User

def test_game_state():
    g = Game("1")
    assert g.state == "Playing"
    g.pause()
    assert g.state == "Paused"
    g.resume()
    assert g.state == "Playing"
    g.game_over()
    assert g.state == "GameOver"

def test_question_logic():
    q = Question("1", "1/2 + 1/2", ["1", "2"], 0)
    assert q.get_prompt() == "1/2 + 1/2"
    assert q.check_answer(0) is True
    assert q.check_answer(1) is False
