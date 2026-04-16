"""
Import all tool modules here to trigger their register() calls at startup.
Add new tools by creating agents/tools/your_tool.py and importing it below.
"""

from agents.tools import add_to_vault    # noqa: F401
from agents.tools import axis_status     # noqa: F401
from agents.tools import cancel_reminder # noqa: F401
from agents.tools import create_note     # noqa: F401
from agents.tools import daily_brief     # noqa: F401
from agents.tools import get_weather     # noqa: F401
from agents.tools import list_reminders  # noqa: F401
from agents.tools import notion_crm      # noqa: F401
from agents.tools import post_to_discord # noqa: F401
from agents.tools import report          # noqa: F401
from agents.tools import send_slack      # noqa: F401
from agents.tools import set_reminder    # noqa: F401
from agents.tools import web_search      # noqa: F401
