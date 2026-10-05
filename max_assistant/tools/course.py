"""Course material tools: answer from the user's slides, PDFs, readings and lecture notes."""
from __future__ import annotations

from .registry import ForLLM, ToolRegistry


def register(reg: ToolRegistry):
    ctx = reg.context
    lib = getattr(ctx, "course", None) if ctx else None
    if lib is None:
        return

    @reg.tool(
        "Search the user's course files (lecture slides, PDFs, readings, handouts, lab instructions) and their "
        "saved lecture notes. Use for questions about course content: 'what did the professor say about RDF schema', "
        "'explain SPARQL OPTIONAL from the lab handout', 'what's on the midterm review sheet'.",
        params={"question": {"type": "string", "description": "The question, in the user's words"},
                "course": {"type": "string", "description": "Optional course, e.g. 'CSE 573'"}},
        required=["question"],
    )
    def course_search(question: str, course: str = ""):
        hits = lib.search(question, k=5, course=course)
        if not hits:
            where = f" for {course}" if course else ""
            return (f"Nothing in the course files{where} matches that. Folders searched: "
                    f"{', '.join(lib.stats()['folders'])}.")
        passages = "\n\n".join(f"[{h.file}, {h.page}] {h.text}" for h in hits)
        return ForLLM("Answer the question in 1-3 spoken sentences using only these passages from the user's course "
                      "files, and say where it's from (file name without the extension, and the page or slide). If they "
                      f"don't answer it, say you couldn't find it in the course files.\n\n{passages}")

    @reg.tool(
        "Which course files Max has indexed (how many per course), e.g. 'what course material do you have'.",
        params={},
        direct=True,
    )
    def course_files():
        s = lib.stats()
        if not s["courses"]:
            return f"No course files yet. Put slides and PDFs in {s['folders'][0]} (one folder per course works best)."
        parts = ", ".join(f"{n} for {c}" for c, n in sorted(s["courses"].items()))
        return f"I have {sum(s['courses'].values())} files: {parts}."

    docs = getattr(ctx, "docnotes", None)
    if docs is None:
        return

    @reg.tool(
        "Read files from the user's course folder in full and write study notes for each one (summary, key "
        "points, deadlines), saved in the Notes tab on the dashboard and phone. Runs in the background. Use for "
        "'summarize my course files', 'make notes of every PDF in my course folder', 'summarize the CSE 573 "
        "slides', 'summarise each of them'. Leave 'file' empty for all files.",
        params={"course": {"type": "string", "description": "Optional course (subfolder), e.g. 'CSE 573'"},
                "file": {"type": "string", "description": "Optional words from ONE file's name; empty = every file"},
                "redo": {"type": "boolean", "description": "Write the notes again even if they already exist"}},
        required=[],
        direct=True,
    )
    def summarize_course_files(course: str = "", file: str = "", redo: bool = False):
        everything = docs.files()
        if not everything:
            return f"Your course folder is empty. Put PDFs, slides or Word files in {lib.folders[0]}."
        items = docs.match(course, file)
        if not items:
            names = ", ".join(p.stem for p, _ in everything[:12])
            return f"No course file matches that. The files I have are: {names}."
        queued, skipped = docs.enqueue(items, redo=redo)
        if not queued:
            if skipped:
                return (f"I already wrote notes for {_names(skipped)}. They're in the Notes tab. "
                        "Say 'redo the notes' to write them again.")
            return "I'm already working on those. I'll tell you when the notes are ready."
        minutes = max(1, round(len(queued) * 0.75))
        text = (f"Writing notes for {_names(queued)}. "
                f"That takes about {minutes} minute{'s' if minutes > 1 else ''}; I'll tell you when they're in the Notes tab.")
        if skipped:
            text += f" {_names(skipped)} already had notes."
        return text


def _names(paths) -> str:
    names = [p.stem for p in paths]
    if len(names) > 4:
        return f"{len(names)} files ({', '.join(names[:3])} and {len(names) - 3} more)"
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]
