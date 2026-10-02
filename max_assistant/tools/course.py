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
