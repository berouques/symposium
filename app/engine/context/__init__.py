"""Сборка промптов и проекция транскрипта (всё, что видит модель).

    common.py     — язык, мета выполнения, форматирование участников
    system.py     — системные промпты хода и голосования
    briefing.py   — брифинг «что изменилось с твоего хода»
    projection.py — транскрипт → chat-сообщения (+ дайджест раунда)
    secretary_msgs.py — задания секретарю

Принципы (унаследованы из v0.3): язык общения идёт только рантайм-метой
(user-роль); служебные [ERROR: …] участникам не показываются; свои реплики
подаются как assistant-сообщения В ТОМ ЖЕ ФОРМАТЕ, что требует протокол
(речь + <state>), чтобы модель видела образец собственных ответов.
"""
from .briefing import build_turn_briefing  # noqa: F401
from .common import build_runtime_meta, human_line, language_name  # noqa: F401
from .projection import build_projection, round_digest, round_transcript_text  # noqa: F401
from .secretary_msgs import (  # noqa: F401
    build_secretary_open_messages, build_secretary_report_messages,
    build_secretary_summarize_messages,
)
from .system import build_member_system_prompt, build_vote_system_prompt  # noqa: F401
