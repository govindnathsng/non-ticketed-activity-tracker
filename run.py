"""Top-level entrypoint so you can run: `python run.py run --dry-run`.

After `pip install`, the same CLI is available as the `activity-tracker`
command (see pyproject.toml [project.scripts]).
"""
from activity_tracker.cli import main

if __name__ == "__main__":
    main()
