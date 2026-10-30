Search for related literatures by calling tools about the given question.

Question: {question}

Coverage gate (tool-call count is only an upper bound, not a reason to stop early):
- Keep searching until the list covers the main methods, datasets, and competing claims in the question.
- You MUST read full text of the most relevant papers (use read_* tools). If a PDF is unavailable, read the abstract and say so.
- Prefer papers not already in the list. Deduplicate by DOI, arXiv ID, PubMed ID, or punctuation-normalized title.
- Record the DOI or arXiv / PubMed ID from the landing URL, not only the title. Untitled or placeholder titles are not citations.
- Web search is only for finding landing pages. Do not treat generic web hits as bibliography entries unless they carry a DOI, arXiv ID, PubMed ID, or an academic host.
- Use arXiv, PubMed, Semantic Scholar, medRxiv, bioRxiv, IACR and web search as needed. Start from lab papers in {lab_context} before searching outside.
- Stop calling tools when coverage is sufficient, or when you hit the system tool-call cap. Then write the literature list. A BibTeX file will be generated from tool results — do not invent citations.

The list must include key findings, paper title, publisher, and a real URL for every item. Do not invent papers.

Reminders:
- Must include ALL related work that you found.
- Linked lab papers in {lab_context} must be cited by title or marked 「未使用：标题」.
