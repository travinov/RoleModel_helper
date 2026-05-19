from __future__ import annotations

from collections import Counter
from datetime import date, datetime
from typing import Any


def _format_datetime(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    text = str(value).replace("T", " ")
    if "+" in text:
        text = text.split("+", 1)[0]
    if "." in text:
        text = text.split(".", 1)[0]
    return text[:19]


def _clean_text(value: object) -> str:
    return str(value or "").replace("\r\n", "\n").replace("\r", "\n").strip()


def _feedback_summary(sessions: list[dict[str, Any]]) -> str:
    counter: Counter[str] = Counter()
    for session in sessions:
        feedback_items = session.get("feedback") or []
        if not feedback_items:
            counter["NO_FEEDBACK"] += 1
            continue
        for feedback in feedback_items:
            counter[str(feedback.get("rating") or "UNKNOWN")] += 1
    if not counter:
        return ""
    return ", ".join(f"{key}: {counter[key]}" for key in sorted(counter))


def build_dialogues_markdown(
    sessions: list[dict[str, Any]],
    date_from: date,
    date_to: date,
) -> str:
    lines: list[str] = []
    lines.append(f"# Диалоги за {date_from.isoformat()} - {date_to.isoformat()}")
    lines.append("")
    lines.append(f"Всего сессий: {len(sessions)}")
    summary = _feedback_summary(sessions)
    if summary:
        lines.append(f"Оценки: {summary}")
    lines.append("")

    for index, session in enumerate(sessions, start=1):
        session_id = str(session.get("session_id") or "")
        short_id = session_id[:8] or str(index)
        lines.append(f"## {index}. Сессия {short_id}")
        lines.append("")
        lines.append(f"- Session ID: `{session_id}`")
        lines.append(f"- Создана: `{_format_datetime(session.get('created_at'))}`")
        lines.append(f"- Обновлена: `{_format_datetime(session.get('updated_at'))}`")
        lines.append(f"- Статус: `{session.get('status') or ''}`")

        feedback_items = session.get("feedback") or []
        if feedback_items:
            lines.append("- Оценки:")
            for feedback in feedback_items:
                rating = str(feedback.get("rating") or "UNKNOWN")
                created_at = _format_datetime(feedback.get("created_at"))
                comment = _clean_text(feedback.get("comment"))
                if comment:
                    lines.append(f"  - `{rating}` `{created_at}`: {comment}")
                else:
                    lines.append(f"  - `{rating}` `{created_at}`")
        else:
            lines.append("- Оценки: нет")

        state = session.get("state") or {}
        context_parts: list[str] = []
        for key, label in (
            ("system_raw", "АС"),
            ("position_raw", "должность"),
            ("city_raw", "город"),
            ("department_raw", "отдел"),
            ("active_goal", "goal"),
            ("conversation_phase", "phase"),
        ):
            value = _clean_text(state.get(key))
            if value:
                context_parts.append(f"{label}: {value}")
        if context_parts:
            lines.append(f"- Последний контекст: {'; '.join(context_parts)}")
        lines.append("")
        lines.append("### Сообщения")
        lines.append("")

        messages = session.get("messages") or []
        if not messages:
            lines.append("_Сообщений нет._")
            lines.append("")
            lines.append("---")
            lines.append("")
            continue

        for message in messages:
            role = str(message.get("role") or "").upper()
            prefix = "Пользователь" if role == "USER" else "Бот" if role == "ASSISTANT" else role.title()
            lines.append(f"**{prefix}** `{_format_datetime(message.get('created_at'))}`")
            lines.append("")
            text = _clean_text(message.get("message_text"))
            lines.append(text if text else "_Пустое сообщение._")
            lines.append("")
        lines.append("---")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"
