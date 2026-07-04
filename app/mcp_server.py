import sys
from datetime import datetime
from mcp.server.fastmcp import FastMCP

# Initialize FastMCP Server
mcp = FastMCP("smart-study-planner-mcp")

# Simple in-memory database for study logs
STUDY_LOGS = {}

@mcp.tool()
def get_exam_countdown(exam_date_str: str) -> str:
    """Calculates the number of days remaining until the exam date.

    Args:
        exam_date_str: The exam date in YYYY-MM-DD format (e.g. 2026-08-15).
    """
    try:
        exam_date = datetime.strptime(exam_date_str.strip(), "%Y-%m-%d")
        now = datetime.now()
        delta = exam_date - now
        days = delta.days + 1
        
        if days < 0:
            return f"The exam date {exam_date_str} has already passed by {-days} days."
        elif days == 0:
            return "The exam is today! Good luck!"
        else:
            return f"There are exactly {days} days left until your exam on {exam_date_str}."
    except ValueError:
        return "Invalid date format. Please use YYYY-MM-DD (e.g. 2026-10-31)."

@mcp.tool()
def get_study_tips(subject: str, difficulty: str) -> str:
    """Provides tailored study techniques based on subject difficulty.

    Args:
        subject: The name of the subject (e.g. Mathematics, History).
        difficulty: The difficulty level: 'High', 'Medium', or 'Low'.
    """
    diff = difficulty.strip().lower()
    tips = f"### Study Tips for {subject} ({difficulty} Difficulty):\n"
    
    if diff == "high":
        tips += (
            "1. **Feynman Technique**: Explain complex concepts in simple terms as if teaching a child.\n"
            "2. **Active Recall**: Do practice questions and flashcards *before* re-reading notes.\n"
            "3. **Chunking**: Break study topics into 25-minute blocks with 5-minute breaks (Pomodoro).\n"
            "4. **Solve Past Exams**: Practice under timed conditions to simulate the pressure."
        )
    elif diff == "medium":
        tips += (
            "1. **Spaced Repetition**: Review the material after 1 day, then 3 days, then 7 days.\n"
            "2. **Mind Mapping**: Draw connections between different topics to build a mental framework.\n"
            "3. **Study Groups**: Discuss difficult concepts with peers to clarify doubts."
        )
    else:
        tips += (
            "1. **Summarization**: Write short summary notes in your own words.\n"
            "2. **Quick Quizzes**: Use end-of-chapter quizzes to confirm understanding.\n"
            "3. **Teaching**: Teach the core outline of the subject to a friend or study partner."
        )
    return tips

@mcp.tool()
def log_study_hours(subject: str, hours: float) -> str:
    """Logs completed study hours for a specific subject and reports total progress.

    Args:
        subject: The name of the subject.
        hours: Number of hours studied (must be positive).
    """
    if hours <= 0:
        return "Hours studied must be a positive number greater than 0."
    
    subj_key = subject.strip().title()
    current_total = STUDY_LOGS.get(subj_key, 0.0)
    new_total = current_total + hours
    STUDY_LOGS[subj_key] = new_total
    
    # Format message for stderr to avoid corrupting stdout (stdio transport requirement)
    print(f"Logged {hours} hours for {subj_key} (New Total: {new_total} hours)", file=sys.stderr)
    
    return (
        f"Successfully logged **{hours} hours** for **{subj_key}**.\n"
        f"Total hours completed for {subj_key}: **{new_total} hours**."
    )

if __name__ == "__main__":
    mcp.run()
