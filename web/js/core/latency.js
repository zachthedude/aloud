/**
 * How long a student took to start answering.
 *
 * One rule, used by the lesson player and the crunch room alike, so every
 * mode feeds the learner model the same measurement. Pure, so it is checked
 * in tools/jscheck.py without a microphone.
 *
 *   asked    when the floor was handed to the student
 *   heard    when the first word was recognised
 *   pressed  when they pressed to talk; the fallback when no words stream in
 *
 * Returns undefined rather than a guess whenever the number would not mean
 * "time spent thinking": a typed answer, no question asked, or a press left
 * over from an earlier turn.
 */
export function latencyFrom({ asked, heard, pressed, inputMode }) {
  if (inputMode !== 'voice' || asked == null) return undefined;
  const started = heard ?? pressed;
  if (started == null || started < asked) return undefined;
  return Math.min(600000, Math.round(started - asked));
}
