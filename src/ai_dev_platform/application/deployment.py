"""Business-facing deployment questions and human approval handling."""

from __future__ import annotations

from ai_dev_platform.domain.models import (
    ConversationAnswer,
    ConversationSession,
    Decision,
    DeploymentConfiguration,
    DeploymentQuestion,
    EvidenceReference,
    TaskRecord,
    WorkflowState,
)
from ai_dev_platform.domain.workflow import assert_transition
from ai_dev_platform.infrastructure.state_store import SQLiteStateStore

DEPLOYMENT_QUESTIONS: tuple[DeploymentQuestion, ...] = (
    DeploymentQuestion(
        id="system_usage_location",
        question="このシステムをどこで利用しますか。",
        benefits=["利用場所に合う安全性と応答性を設計できます。"],
        drawbacks=["場所を広げるほど認証とネットワーク設計が複雑になります。"],
        recommendation="利用場所を最小範囲から指定する",
        recommendation_reason="初期費用と攻撃面を抑えられるためです。",
    ),
    DeploymentQuestion(
        id="users",
        question="誰が利用しますか。",
        benefits=["必要な権限を役割ごとに限定できます。"],
        drawbacks=["利用者区分が多いほど権限管理の運用負荷が増えます。"],
        recommendation="利用者を役割単位で列挙する",
        recommendation_reason="個人名を保存せず権限設計できるためです。",
    ),
    DeploymentQuestion(
        id="usage_frequency",
        question="どの程度の頻度で利用しますか。",
        benefits=["容量と費用を現実的に見積もれます。"],
        drawbacks=["将来の急増には再見積りが必要です。"],
        recommendation="平常時と繁忙時を分けて回答する",
        recommendation_reason="過剰構成と性能不足の両方を避けやすいためです。",
    ),
    DeploymentQuestion(
        id="idle_shutdown_policy",
        question="利用されない時間は停止して費用を抑えますか。",
        benefits=["従量課金を抑えられます。"],
        drawbacks=["再開操作と待ち時間が発生します。"],
        recommendation="夜間・休日停止をまず検討する",
        recommendation_reason="常時稼働要件がなければ費用効果が高いためです。",
    ),
    DeploymentQuestion(
        id="restart_wait_tolerance",
        question="停止後の再起動待ちを許容できますか。",
        benefits=["停止運用の選択肢が広がります。"],
        drawbacks=["緊急利用時にも待ち時間が生じます。"],
        recommendation="許容分数を明記する",
        recommendation_reason="起動方式の品質条件にできるためです。",
    ),
    DeploymentQuestion(
        id="preproduction_environment",
        question="本番前の確認環境を用意しますか。",
        benefits=["本番前に統合確認できます。"],
        drawbacks=["環境費用と管理作業が増えます。"],
        recommendation="本番と分離した確認環境を用意する",
        recommendation_reason="変更による本番事故を減らせるためです。",
    ),
    DeploymentQuestion(
        id="release_downtime_tolerance",
        question="本番リリース時の停止を許容できますか。",
        benefits=["許容できれば更新方式を簡素化できます。"],
        drawbacks=["停止中は業務を継続できません。"],
        recommendation="許容時間帯と最大時間を決める",
        recommendation_reason="リリース方式と利用者周知を設計できるためです。",
    ),
    DeploymentQuestion(
        id="recovery_time_objective",
        question="障害時に許容できる停止時間はどの程度ですか。",
        benefits=["復旧体制を数値で設計できます。"],
        drawbacks=["短いほど費用と運用負荷が増えます。"],
        recommendation="業務影響から復旧目標時間を決める",
        recommendation_reason="技術都合ではなく業務損失に合わせられるためです。",
    ),
    DeploymentQuestion(
        id="recovery_point_objective",
        question="障害時に許容できるデータ消失量はどの程度ですか。",
        benefits=["バックアップ間隔を決められます。"],
        drawbacks=["消失ゼロに近づけるほど費用が増えます。"],
        recommendation="時間または件数で上限を指定する",
        recommendation_reason="復旧確認の受入条件にできるためです。",
    ),
    DeploymentQuestion(
        id="production_like_data_policy",
        question="本番相当データを検証に使いますか。",
        benefits=["実運用に近い条件を確認できます。"],
        drawbacks=["漏えいと目的外利用のリスクが大きくなります。"],
        recommendation="原則として合成データを使う",
        recommendation_reason="通常のAI・Git・Artifactへ本番相当データを持ち込まないためです。",
    ),
    DeploymentQuestion(
        id="monthly_cost_policy",
        question="月額費用の上限や優先方針はありますか。",
        benefits=["構成選択を予算内に制御できます。"],
        drawbacks=["上限が厳しい場合は可用性や性能との調整が必要です。"],
        recommendation="警告額と停止判断額を指定する",
        recommendation_reason="費用超過前に人間判断へ戻せるためです。",
    ),
    DeploymentQuestion(
        id="operations_owner",
        question="運用判断を担当する役割は誰ですか。",
        benefits=["障害・費用・リリース判断の責任が明確になります。"],
        drawbacks=["担当不在時の代替手順も必要です。"],
        recommendation="主担当と代替担当の役割を指定する",
        recommendation_reason="自動化が人間責任を曖昧にしないためです。",
    ),
)


def start_deployment_session(project_name: str, issue_number: int) -> ConversationSession:
    """Create a stable Issue-scoped deployment conversation."""
    return ConversationSession(
        session_id=f"{project_name}:issue-{issue_number}:deployment",
        project_name=project_name,
        issue_number=issue_number,
        questions=list(DEPLOYMENT_QUESTIONS),
    )


def unanswered_questions(session: ConversationSession) -> list[DeploymentQuestion]:
    """Return only unanswered questions, preventing duplicate prompts."""
    answered = {answer.question_id for answer in session.answers}
    questions = session.questions or list(DEPLOYMENT_QUESTIONS)
    return [question for question in questions if question.id not in answered]


def record_answer(
    store: SQLiteStateStore,
    session: ConversationSession,
    *,
    question_id: str,
    answer: str,
    answered_by: str,
) -> ConversationSession:
    """Persist one sanitized human answer; an existing answer is not duplicated."""
    known = {question.id for question in DEPLOYMENT_QUESTIONS}
    if question_id not in known:
        raise ValueError("unknown deployment question")
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


def approve_deployment_configuration(
    store: SQLiteStateStore,
    session: ConversationSession,
    *,
    approver: str,
    github_record_id: str,
) -> TaskRecord:
    """Structure complete answers and resume only after explicit human approval."""
    missing = unanswered_questions(session)
    if missing:
        raise ValueError("all deployment questions must be answered before approval")
    if session.issue_number is None:
        raise ValueError("deployment approval requires an Issue-scoped session")
    if not github_record_id:
        raise ValueError("a formal GitHub approval record is required")
    answers = {answer.question_id: answer.answer for answer in session.answers}
    task = store.get_task_by_issue(session.issue_number)
    configuration = DeploymentConfiguration(
        decision=Decision.PASS,
        summary="Deployment and environment configuration approved by a human.",
        evidence=[
            EvidenceReference(
                id="deployment-human-approval",
                kind="github",
                reference=github_record_id,
                safe_summary="Human-confirmed deployment decisions.",
            )
        ],
        deployment_target={
            "usage_location": answers["system_usage_location"],
            "users": answers["users"],
            "usage_frequency": answers["usage_frequency"],
            "idle_shutdown_policy": answers["idle_shutdown_policy"],
            "restart_wait_tolerance": answers["restart_wait_tolerance"],
        },
        environments=[
            {"name": "production", "release_downtime": answers["release_downtime_tolerance"]},
            {"name": "preproduction", "policy": answers["preproduction_environment"]},
        ],
        availability_requirements={
            "release_downtime": answers["release_downtime_tolerance"],
        },
        recovery_requirements={
            "rto": answers["recovery_time_objective"],
            "rpo": answers["recovery_point_objective"],
        },
        cost_policy={"monthly": answers["monthly_cost_policy"]},
        restricted_data_policy={"production_like_data": answers["production_like_data_policy"]},
        human_approved=True,
        approver=approver,
    )
    evidence = task.evidence.model_copy(
        update={"deployment_configuration": configuration}, deep=True
    )
    updated = task.model_copy(update={"evidence": evidence, "pending_human_decisions": []})
    if task.state == WorkflowState.DEPLOYMENT_CONFIGURATION_REQUIRED:
        assert_transition(task.state, WorkflowState.DEPLOYMENT_CONFIGURATION)
        updated = updated.model_copy(update={"state": WorkflowState.DEPLOYMENT_CONFIGURATION})
    saved = store.save_task(updated)
    store.append_event(
        saved.task_id,
        approver,
        "deployment_configuration_approved",
        "recorded",
        {"issue_number": saved.issue_number, "commit_sha": saved.commit_sha},
    )
    return saved
