"""Interaction tools — structured UI elements rendered in the conversation.

When an agent calls these tools, the result is returned immediately
and the structured data (question, options, buttons) is attached to
the assistant's ``done`` event as ``ui_elements`` metadata.  The
frontend renders these as interactive elements in the conversation
(buttons, option lists, etc.).

The user's response is simply their next message — either clicking
an option (which sends the option text) or typing freely.  No special
endpoint or blocking mechanism is needed.

The agent can call ``ask_user`` multiple times in the same turn to
ask several questions at once.  The frontend renders all questions
together as a form and collects all answers before continuing.

Flow::

    1. Agent calls ask_user(...) one or more times (parallel tool calls)
    2. Each tool returns immediately
    3. done event carries ui_elements: [{type: "ask", ...}, {type: "ask", ...}, ...]
    4. Frontend renders all questions as a form below the assistant message
    5. User answers all questions → single user message with all answers
    6. Conversation continues normally
"""

from __future__ import annotations

import json

from acai.orchestrator.tools import tool

# Tool names recognized as interaction tools (handled with special SSE metadata)
INTERACTION_TOOLS = frozenset({
    "interaction_ask_user",
    "interaction_confirm",
    "interaction_notify",
})


@tool(permissions=("read",), resources=("interaction:ask",))
def ask_user(
    question: str,
    options: str = "[]",
    allow_free_text: bool = True,
    select_mode: str = "single",
    context: str = "",
    question_id: str = "",
) -> str:
    """Ask the user a question with suggested options.

    The question will be displayed as an interactive UI element in the
    conversation.  The user can click one of the options or type a
    custom answer.  Their response arrives as the tool result.

    Args:
        question: The question to ask the user.
        options: JSON array of option objects, each with "id" and "label" keys.
                 Example: '[{"id": "opt1", "label": "Option A"}, {"id": "opt2", "label": "Option B"}]'
        allow_free_text: If True, user can type a custom answer beyond the options.
        select_mode: "single" for radio-style (pick one) or "multiple" for
                     checkbox-style (pick several).  Default "single".
        context: Optional context/explanation to show with the question.
        question_id: Optional ID to identify this question when multiple are asked at once.

    Returns:
        The user's answer (selected option label or typed text).
    """
    try:
        parsed_options = json.loads(options) if options else []
    except (json.JSONDecodeError, TypeError):
        parsed_options = []

    element: dict = {
        "type": "ask",
        "question": question,
        "options": parsed_options,
        "allow_free_text": allow_free_text,
        "select_mode": select_mode if select_mode in ("single", "multiple") else "single",
        "context": context,
    }
    if question_id:
        element["id"] = question_id

    return json.dumps({
        "displayed": True,
        "ui_element": element,
    })


@tool(permissions=("read",), resources=("interaction:ask",))
def confirm(
    message: str,
    confirm_label: str = "Yes",
    deny_label: str = "No",
) -> str:
    """Ask the user for a yes/no confirmation.

    Displays a confirmation prompt with two buttons.  The user's click
    arrives as their next message ("Yes" or "No").

    After calling this, finish your turn with the confirmation question
    in your message text.

    Args:
        message: What you're asking the user to confirm.
        confirm_label: Label for the confirm button (default "Yes").
        deny_label: Label for the deny button (default "No").

    Returns:
        Confirmation that the prompt UI has been queued for display.
    """
    return json.dumps({
        "displayed": True,
        "ui_element": {
            "type": "confirm",
            "message": message,
            "options": [
                {"id": "yes", "label": confirm_label},
                {"id": "no", "label": deny_label},
            ],
        },
    })


@tool(permissions=("read",), resources=("interaction:notify",))
def notify(
    message: str,
    level: str = "info",
    title: str = "",
) -> str:
    """Send a notification/toast to the user.

    Displayed as a transient notification (toast) in the UI, not
    as a conversation message.  Does not require a response.

    Args:
        message: The notification message.
        level: Severity level — "info", "success", "warning", or "error".
        title: Optional title for the notification.

    Returns:
        Confirmation that the notification was sent.
    """
    return json.dumps({
        "displayed": True,
        "ui_element": {
            "type": "notify",
            "message": message,
            "level": level,
            "title": title,
        },
    })
