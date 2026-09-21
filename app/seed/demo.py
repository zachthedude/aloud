"""Demo data.

The sample subject is built by running the real pipeline over real material:
the text below is written to a file, uploaded through the same validation,
extracted, chunked, analysed and turned into lessons by whichever teaching
engine is configured.

That matters. If seeding wrote hand-authored lessons straight into the
database, the demo would prove nothing about whether the product works. This
way, opening the demo is a genuine test of the ingestion path.
"""

from __future__ import annotations

from datetime import timedelta

from ..api.deps import build_container, hash_password
from ..config import Settings
from ..domain.models import (
    LearningSpace,
    LearningSession,
    LessonState,
    Mastery,
    ProcessingStatus,
    Resource,
    ResourceKind,
    User,
    new_id,
    utcnow,
)
from ..services.documents import pipeline

DEMO_EMAIL = "student@aloud.app"
DEMO_PASSWORD = "learnaloud"
DEMO_NAME = "Sam"

SPACE_TITLE = "Grade 10 Physics"
SPACE_LEVEL = "Grade 10"

# Written the way a school textbook chapter is written: headings, definitions,
# worked examples and explicit warnings about common errors. The analyser finds
# structure that is genuinely there rather than structure planted for it.
FORCES_AND_MOTION = """# Forces and Motion

## Force
A force is a push or a pull that acts on an object. Forces are measured in newtons, written N.
A force is a vector quantity, which means it has both a size and a direction. Forces can change
the speed of an object, change its direction, or change its shape. You cannot see a force
itself, only what it does. When you push a shopping trolley, lean on a wall, or drop a book,
forces are acting.

## Balanced and Unbalanced Forces
When the forces acting on an object are balanced, they cancel out and the object does not change
its motion. It stays at rest, or it keeps moving at a steady speed in a straight line. When the
forces are unbalanced, there is a resultant force. The resultant force is the single force that
has the same effect as all the forces added together. An unbalanced force always causes a change
in motion. For example, a book resting on a table has its weight pulling down and the normal
contact force pushing up, and these are balanced, so the book stays still.

## Newton's First Law
Newton's first law states that an object will remain at rest, or continue to move at a constant
velocity, unless acted on by a resultant force. This tendency of an object to resist a change in
its motion is called inertia. Inertia depends on mass: the greater the mass, the greater the
inertia. A common misconception is that a moving object needs a continuous force to keep it
moving. In fact a moving object keeps moving on its own, and only slows down because friction
and air resistance act on it. For example, a puck sliding on frictionless ice would continue at
the same speed forever.

## Newton's Second Law
Newton's second law describes what happens when the resultant force is not zero. It relates the
resultant force on an object to the acceleration it produces. The law is written as F = m a,
where F is the resultant force in newtons, m is the mass in kilograms, and a is the acceleration
in metres per second squared. Acceleration is directly proportional to the resultant force, so
doubling the force doubles the acceleration. Acceleration is inversely proportional to the mass,
so doubling the mass halves the acceleration. For example, a trolley of mass 2 kilograms pushed
with a resultant force of 10 newtons accelerates at 5 metres per second squared. Students often
confuse mass and weight. Mass is the amount of matter in an object and is measured in kilograms,
while weight is the force of gravity acting on that mass and is measured in newtons.

## Newton's Third Law
Newton's third law states that for every action there is an equal and opposite reaction. When
object A exerts a force on object B, object B exerts a force of the same size on object A, in
the opposite direction. It is important to remember that these two forces act on different
objects, which is why they never cancel each other out. Do not confuse a third law pair with
balanced forces. For instance, when you walk, you push backwards on the ground and the ground
pushes forwards on you, and it is that forward push that moves you.

## Friction
Friction is a force that opposes the relative motion of two surfaces in contact. Friction always
acts in the direction opposite to the motion. Friction converts kinetic energy into thermal
energy, which is why rubbing your hands together makes them warm. Air resistance is a type of
friction that acts on objects moving through air, and it increases as speed increases. Friction
is not always unhelpful, because without it you could not walk or grip anything.

## Terminal Velocity
When an object falls, gravity pulls it down and air resistance pushes up against it. At first
the object accelerates, because the downward force is larger. As it speeds up, air resistance
increases. Eventually air resistance becomes equal to weight, the forces are balanced, the
resultant force is zero, and the object stops accelerating. The steady speed it then falls at is
called terminal velocity. A skydiver reaches terminal velocity before opening a parachute, and
opening the parachute increases air resistance so a new, lower terminal velocity is reached.

## Momentum
Momentum is a measure of how difficult it is to stop a moving object. Momentum is calculated as
p = m v, where p is momentum, m is mass and v is velocity. Momentum depends on both mass and
velocity, so a slow lorry can have more momentum than a fast bicycle. In a collision, the total
momentum before the collision is equal to the total momentum after it, provided no external
force acts. This is called conservation of momentum. Crumple zones in cars work by increasing
the time taken to change momentum, which reduces the force on the passengers.
"""


#: A believable history behind each seeded state, folded through the real
#: learner maths so the demo's confidence, hesitation and quarantine are what
#: the product would genuinely have worked out, not numbers typed in.
DEMO_HISTORY = {
    Mastery.MASTERED: [("correct", 1400)] * 4,
    Mastery.UNDERSTOOD: [("correct", 1900), ("correct", 2300), ("correct", 1700)],
    Mastery.DEVELOPING: [("correct", 6800), ("partial", 7400), ("correct", 7900)],
    Mastery.NEEDS_REVIEW: [("wrong", 5200), ("missed", None), ("wrong", 6100)],
}


def with_history(record, state, *, seen_at):
    """The learner record after the demo history for `state`."""
    from ..domain import learner
    from ..domain.learner import Observation

    fillers = {"partial": "um I think it's, uh", "missed": "I don't know"}
    for outcome, latency in DEMO_HISTORY[state]:
        record = learner.record(
            record, Observation(outcome, latency_ms=latency, transcript=fillers.get(outcome, "")),
            now=seen_at,
        )
    record.evidence_count = len(DEMO_HISTORY[state])
    return record


def seed(settings: Settings, *, with_progress: bool = True) -> dict:
    """Create the demo account and subject. Safe to run more than once."""
    container = build_container(settings)
    repos = container.repos

    user = repos.users.by_email(DEMO_EMAIL)
    if user is None:
        user = User(
            id=new_id("usr"),
            email=DEMO_EMAIL,
            display_name=DEMO_NAME,
            password_hash=hash_password(DEMO_PASSWORD),
            preferred_speed=1.0,
            onboarded=True,
        )
        repos.users.create(user)

    existing = [s for s in repos.spaces.list_for_user(user.id) if s.title == SPACE_TITLE]
    if existing and repos.lessons.list_for_space(existing[0].id):
        space = existing[0]
        return {
            "title": space.title,
            "email": DEMO_EMAIL,
            "password": DEMO_PASSWORD,
            "spaceId": space.id,
            "concepts": len(repos.concepts.list_for_space(space.id)),
            "lessons": len(repos.lessons.list_for_space(space.id)),
            "created": False,
        }

    space = existing[0] if existing else repos.spaces.create(
        LearningSpace(
            id=new_id("spc"),
            user_id=user.id,
            title=SPACE_TITLE,
            level=SPACE_LEVEL,
            description="Forces, Newton's laws, friction and momentum.",
            accent=0,
        )
    )

    # Write the material and register it exactly as an upload would.
    if not repos.resources.list_for_space(space.id):
        resource_id = new_id("res")
        stored = pipeline.store_upload(
            FORCES_AND_MOTION.encode("utf-8"),
            resource_id=resource_id,
            extension=".md",
            directory=settings.uploads_dir / space.id,
        )
        repos.resources.create(
            Resource(
                id=resource_id,
                space_id=space.id,
                title="Forces and Motion - class notes",
                kind=ResourceKind.MARKDOWN,
                original_filename="forces-and-motion.md",
                stored_path=str(stored),
                byte_size=len(FORCES_AND_MOTION.encode("utf-8")),
                status=ProcessingStatus.QUEUED,
            )
        )

    # Run the real analysis path.
    result = container.curriculum.rebuild(space.id, user.id)

    if with_progress:
        _seed_progress(container, user, space)

    container.shutdown()
    return {
        "title": space.title,
        "email": DEMO_EMAIL,
        "password": DEMO_PASSWORD,
        "spaceId": space.id,
        "concepts": result["concepts"],
        "lessons": result["lessons"],
        "created": True,
    }


def _seed_progress(container, user: User, space: LearningSpace) -> None:
    """A plausible history, so the dashboard is not empty on first open.

    Two concepts settled, one still building, one flagged for revisiting, and a
    lesson left part-finished so "continue where you left off" has something to
    show.
    """
    repos = container.repos
    lessons = repos.lessons.list_for_space(space.id)
    if not lessons:
        return

    shape = [Mastery.MASTERED, Mastery.UNDERSTOOD, Mastery.DEVELOPING, Mastery.NEEDS_REVIEW]
    notes = {
        Mastery.NEEDS_REVIEW: "Mixed up mass and weight when explaining it back.",
        Mastery.DEVELOPING: "",
    }

    for index, lesson in enumerate(lessons[: len(shape)]):
        record = repos.mastery.get_or_create(user.id, space.id, lesson.concept_id)
        state = shape[index]
        record = with_history(record, state, seen_at=utcnow() - timedelta(hours=2 + index * 3))
        record.state = state
        record.correct_streak = 2 if state is Mastery.MASTERED else 0
        record.struggle_count = 2 if state is Mastery.NEEDS_REVIEW else 0
        record.note = notes.get(state, "")
        repos.mastery.save(record)

        session = repos.sessions.create(
            LearningSession(
                id=new_id("ses"),
                user_id=user.id,
                space_id=space.id,
                lesson_id=lesson.id,
                state=LessonState.COMPLETED,
                completed=True,
                section_ordinal=len(repos.lessons.sections(lesson.id)),
            )
        )
        session.updated_at = utcnow() - timedelta(days=index + 1)
        repos.sessions.save(session)

    # One lesson left part-way through.
    if len(lessons) > len(shape):
        in_progress = lessons[len(shape)]
        sections = repos.lessons.sections(in_progress.id)
        session = repos.sessions.create(
            LearningSession(
                id=new_id("ses"),
                user_id=user.id,
                space_id=space.id,
                lesson_id=in_progress.id,
                state=LessonState.PAUSED,
                section_ordinal=max(1, len(sections) // 3),
                position_seconds=14.0,
            )
        )
        repos.sessions.save(session)
