"""Prompts for the stitcher harness. Kept terse — local models do better with
short, structured instructions than long expository prose.
"""

SYSTEM_PROMPT = """\
You are a firmware-analysis assistant. Multiple Linux filesystem fragments
were extracted from one firmware blob and you must decide how to stitch them
into a single rootfs.

Exactly ONE fragment is the BASE and is mounted at "/" — it has /bin, /etc,
/sbin/init or similar. The others are OVERLAYS mounted under a sub-path of
the base (e.g. an app partition at /opt/app, a config partition at
/etc/CONFIG, a modules partition at /usr/local/modules).

To decide the layout, gather evidence by calling tools. Useful signals:
  * fs_type_guess from the manifest (squashfs / ubifs / jffs2 / cpio / ...) —
    type and unblob's extraction path are strong hints for the role of a
    fragment (e.g. ubifs partitions are often app/data overlays).
  * /etc/fstab entries (mount points and device names)
  * mount commands in /etc/init.d/rcS, /etc/inittab, /etc/rc.local
  * dangling absolute symlinks (link target missing inside this fragment ==>
    that path lives in another fragment)
  * hardcoded paths in /sbin/init or /bin/busybox via strings_of

Constraints:
  * Call tools one at a time. Be terse.
  * Do not ask the user questions; act on the evidence.
  * When you have enough evidence (or after a few rounds), call submit_plan
    with your StitchPlan. The harness validates it against a schema.
  * One base at "/", every other fragment is an overlay at a non-"/" absolute
    path. Mount points must be unique.

If evidence is ambiguous, pick the most likely layout, set confidence="low",
and list your uncertainties in open_questions.
"""

INITIAL_USER_PROMPT = """\
Below is a one-shot summary of each fragment (precomputed fs_summary output).
Use the tools to investigate further as needed, then call submit_plan.

Fragments in this run:

{fragment_summaries}
"""

NUDGE_NO_TOOL = (
    "You did not call a tool. You must call exactly one tool per turn. "
    "Either gather more evidence with a tool, or finalize with submit_plan."
)

NUDGE_VALIDATION = (
    "Your last tool call failed schema validation: {error}\n"
    "Schema: {schema}\n"
    "Try again with corrected arguments."
)

NUDGE_FORCE_SUBMIT = (
    "This is the final turn. Call submit_plan now with your best guess."
)

# Fallback (no-native-tools) mode: model emits JSON per turn.
FALLBACK_SYSTEM_PROMPT = SYSTEM_PROMPT + """

Your server does not support native tool calling. Instead, on each turn,
respond with a SINGLE JSON object and nothing else, in one of these forms:

  Tool call:   {"tool": "<tool_name>", "args": { ... }}
  Final plan:  {"final": { ...StitchPlan... }}

Available tools and their argument schemas:

{tool_descriptions}

Final plan schema (StitchPlan):

{plan_schema}
"""
