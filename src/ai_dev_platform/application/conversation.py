"""Business-oriented request structuring and Issue rendering."""

from __future__ import annotations

from ai_dev_platform.domain.models import (
    ConversationAnswer,
    ConversationSession,
    DeploymentQuestion,
    IssueDraft,
)
from ai_dev_platform.infrastructure.state_store import SQLiteStateStore

GENERAL_DECISION_QUESTIONS: tuple[DeploymentQuestion, ...] = (
    DeploymentQuestion(
        id="business-requirements",
        question="実現すべき業務結果と、変えてはいけない業務ルールは何ですか。",
        benefits=["実装と業務目的のずれを防げます。"],
        drawbacks=["合意に時間が必要です。"],
        recommendation="結果と禁止事項を具体例で確定する",
        recommendation_reason="受入条件と業務レビューの根拠にできるためです。",
    ),
    DeploymentQuestion(
        id="deployment-configuration",
        question="利用場所、環境、可用性、復旧、費用、運用担当をどうしますか。",
        benefits=["設計とQAを実運用条件に合わせられます。"],
        drawbacks=["選択肢ごとの費用と運用負荷を比較する必要があります。"],
        recommendation="deployment-questionsの全項目へ回答する",
        recommendation_reason="未確定のまま最終設計へ進まないためです。",
    ),
    DeploymentQuestion(
        id="quality-criteria",
        question="合格と判断するテスト、期待結果、許容できない不具合は何ですか。",
        benefits=["レビューとQAの判定が再現可能になります。"],
        drawbacks=["固定評価ケースの維持が必要です。"],
        recommendation="critical・majorの具体例と必須テストを確定する",
        recommendation_reason="AIが品質基準を都合よく変更できないためです。",
    ),
)


def start_conversation_session(
    project_name: str, issue_number: int | None = None
) -> ConversationSession:
    """Create a project- or Issue-scoped structured conversation."""
    scope = f"issue-{issue_number}" if issue_number is not None else "project"
    return ConversationSession(
        session_id=f"{project_name}:{scope}:conversation",
        project_name=project_name,
        issue_number=issue_number,
        questions=list(GENERAL_DECISION_QUESTIONS),
    )


def unanswered_decisions(session: ConversationSession) -> list[DeploymentQuestion]:
    """Return unresolved decisions without repeating answered questions."""
    answered = {answer.question_id for answer in session.answers}
    return [question for question in session.questions if question.id not in answered]


def record_conversation_answer(
    store: SQLiteStateStore,
    session: ConversationSession,
    *,
    question_id: str,
    answer: str,
    answered_by: str,
) -> ConversationSession:
    """Persist a sanitized decision answer once."""
    if question_id not in {question.id for question in session.questions}:
        raise ValueError("unknown conversation decision")
    if any(item.question_id == question_id for item in session.answers):
        return session
    updated = session.model_copy(
        update={
            "answers": [
                *session.answers,
                ConversationAnswer(
                    question_id=question_id,
                    answer=answer,
                    answered_by=answered_by,
                ),
            ]
        }
    )
    return store.save_conversation_session(updated)


def render_confirmed_decisions(session: ConversationSession) -> str:
    """Render only explicitly answered decisions for GitHub synchronization."""
    if not session.answers:
        raise ValueError("no confirmed decision is available for synchronization")
    values = "\n".join(f"- `{answer.question_id}`: {answer.answer}" for answer in session.answers)
    return (
        "<!-- ai-dev-confirmed-start -->\n"
        f"## Confirmed human decisions\n\n{values}\n"
        "<!-- ai-dev-confirmed-end -->"
    )


def merge_confirmed_decisions(existing_body: str, confirmed_section: str) -> str:
    """Replace only the managed confirmed section and preserve all other Issue text."""
    start = "<!-- ai-dev-confirmed-start -->"
    end = "<!-- ai-dev-confirmed-end -->"
    if start in existing_body and end in existing_body:
        prefix, remainder = existing_body.split(start, maxsplit=1)
        _, suffix = remainder.split(end, maxsplit=1)
        return f"{prefix.rstrip()}\n\n{confirmed_section}{suffix}"
    return f"{existing_body.rstrip()}\n\n{confirmed_section}\n"


def structure_request(request: str) -> IssueDraft:
    """Create a conservative local Issue draft from natural language.

    This fallback deliberately marks missing acceptance details for human confirmation.
    An AgentProvider can later replace it without changing the Issue contract.
    """
    normalized = " ".join(request.split())
    title = normalized[:117] + "..." if len(normalized) > 120 else normalized
    return IssueDraft(
        title=title or "要件の確認が必要な開発依頼",
        purpose=normalized,
        scope=[normalized],
        business_requirements=[normalized],
        acceptance_criteria=["依頼内容を満たすことを、テスト結果とレビュー結果で確認できる"],
        business_impact="依頼内容の実現により対象業務が変更されます。詳細確認が必要です。",
        user_impact="利用者の操作または結果に影響する可能性があります。",
        data_impact="入力・保存・出力するデータの分類を設計前に確認します。",
        security_impact="認証、権限、外部接続、機密情報の有無を設計前に確認します。",
        production_impact="本番反映には別途、対象Issue・工程・commit SHAを伴う承認が必要です。",
        quality_risks=["受入条件と模範データが未確定の場合、品質判定に十分な証拠を得られない"],
        human_decisions_required=[
            "業務要件と受入条件の最終確定",
            "デプロイ先と環境構成",
            "期待結果と品質基準",
        ],
    )


def render_issue_body(draft: IssueDraft) -> str:
    """Render all required GitHub Issue sections."""

    def bullets(values: list[str]) -> str:
        return "\n".join(f"- {value}" for value in values) if values else "- なし"

    return f"""## 目的
{draft.purpose}

## 背景
{draft.background or "要確認"}

## 現在の問題
{draft.current_problem or "要確認"}

## 対象範囲
{bullets(draft.scope)}

## 対象外
{bullets(draft.out_of_scope)}

## 業務要件
{bullets(draft.business_requirements)}

## 機能要件
{bullets(draft.functional_requirements)}

## 非機能要件
{bullets(draft.non_functional_requirements)}

## 受入条件
{bullets(draft.acceptance_criteria)}

## 制約
{bullets(draft.constraints)}

## 利用者への影響
{draft.user_impact or "要確認"}

## 業務への影響
{draft.business_impact or "要確認"}

## データへの影響
{draft.data_impact or "要確認"}

## セキュリティへの影響
{draft.security_impact or "要確認"}

## 本番への影響
{draft.production_impact or "要確認"}

## 想定される品質リスク
{bullets(draft.quality_risks)}

## 関連資料
{bullets(draft.references)}

## 人間判断が必要な事項
{bullets(draft.human_decisions_required)}
"""
