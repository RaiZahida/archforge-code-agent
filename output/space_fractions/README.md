# Space Fractions

A desktop-based interactive learning tool for sixth-grade students to improve fraction-solving skills.

## Architecture Mapping

| UML Element | Implementation Module |
| :--- | :--- |
| Game | `app/models.py` |
| Question | `app/models.py` |
| User | `app/models.py` |
| Admin | `app/models.py` |
| GameComponent | `app/game_logic.py` |
| QuestionComponent | `app/persistence.py` |
| UI | `app/ui/` |

## How to Run
```bash
python3 main.py
```

## How to Test
```bash
pytest
```
