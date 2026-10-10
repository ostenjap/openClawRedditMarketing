#!/usr/bin/env python3
"""Make langchain_text_splitters and langchain_core import transformers / sentence_transformers / spacy lazily.

Open WebUI imports these at startup; its eager optional imports pull in
PyTorch (~300-400 MB) even when no local embedding model is used. This swaps each eager
`try: import X` for an importlib.util.find_spec() availability check and imports X inside
the one function that actually needs it. Behaviour is unchanged when the splitter is used.

Usage: python3 lazy_torch_patch.py <site-packages dir>   (idempotent)
"""
import sys
from pathlib import Path

MARK = "# lazy-torch-patch"

PATCHES = {
    "langchain_text_splitters/base.py": [
        (
            """try:
    from transformers.tokenization_utils_base import PreTrainedTokenizerBase

    _HAS_TRANSFORMERS = True
except ImportError:
    _HAS_TRANSFORMERS = False
""",
            f"""import importlib.util  {MARK}

_HAS_TRANSFORMERS = importlib.util.find_spec("transformers") is not None
""",
        ),
        (
            """            raise ValueError(msg)

        if not isinstance(tokenizer, PreTrainedTokenizerBase):""",
            f"""            raise ValueError(msg)

        from transformers.tokenization_utils_base import PreTrainedTokenizerBase  {MARK}

        if not isinstance(tokenizer, PreTrainedTokenizerBase):""",
        ),
    ],
    "langchain_text_splitters/sentence_transformers.py": [
        (
            """try:
    # Type ignores needed as long as sentence-transformers doesn't support Python 3.14.
    from sentence_transformers import (  # type: ignore[import-not-found, unused-ignore]
        SentenceTransformer,
    )

    _HAS_SENTENCE_TRANSFORMERS = True
except ImportError:
    _HAS_SENTENCE_TRANSFORMERS = False
""",
            f"""import importlib.util  {MARK}

_HAS_SENTENCE_TRANSFORMERS = importlib.util.find_spec("sentence_transformers") is not None
""",
        ),
        (
            """        self._model = SentenceTransformer(""",
            f"""        from sentence_transformers import SentenceTransformer  {MARK}

        self._model = SentenceTransformer(""",
        ),
    ],
    "langchain_text_splitters/spacy.py": [
        (
            """try:
    # Type ignores needed as long as spacy doesn't support Python 3.14.
    import spacy  # type: ignore[import-not-found, unused-ignore]
    from spacy.lang.en import English  # type: ignore[import-not-found, unused-ignore]

    if TYPE_CHECKING:
        from spacy.language import (  # type: ignore[import-not-found, unused-ignore]
            Language,
        )

    _HAS_SPACY = True
except ImportError:
    _HAS_SPACY = False
""",
            f"""import importlib.util  {MARK}

if TYPE_CHECKING:
    from spacy.language import (  # type: ignore[import-not-found, unused-ignore]
        Language,
    )

_HAS_SPACY = importlib.util.find_spec("spacy") is not None
""",
        ),
    ],
    "langchain_core/language_models/base.py": [
        (
            """try:
    from transformers import GPT2TokenizerFast  # type: ignore[import-not-found]

    _HAS_TRANSFORMERS = True
except ImportError:
    _HAS_TRANSFORMERS = False
""",
            f"""import importlib.util  {MARK}

_HAS_TRANSFORMERS = importlib.util.find_spec("transformers") is not None
""",
        ),
        (
            """    # create a GPT-2 tokenizer instance
    return GPT2TokenizerFast.from_pretrained("gpt2")""",
            f"""    from transformers import GPT2TokenizerFast  {MARK}

    # create a GPT-2 tokenizer instance
    return GPT2TokenizerFast.from_pretrained("gpt2")""",
        ),
    ],
}


def main(d: Path) -> None:
    for name, reps in PATCHES.items():
        f = d / name
        src = f.read_text()
        if MARK in src:
            print(f"{name}: already patched")
            continue
        for old, new in reps:
            if src.count(old) != 1:
                sys.exit(f"{name}: anchor not found exactly once, aborting (nothing written for this file)")
            src = src.replace(old, new)
        if name.endswith("spacy.py"):
            # spacy / English are used inside one helper; import them there.
            old = "    if not _HAS_SPACY:"
            if src.count(old) != 1:
                sys.exit("spacy.py: _HAS_SPACY guard not found exactly once")
            lines = src.split("\n")
            i = next(n for n, l in enumerate(lines) if l == old)
            # find end of the guard's raise block: first following line at 4-space indent that isn't blank
            j = i + 1
            while lines[j].startswith("        ") or not lines[j].strip():
                j += 1
            lines.insert(j, f"    import spacy  {MARK}\n    from spacy.lang.en import English  {MARK}\n")
            src = "\n".join(lines)
        f.write_text(src)
        print(f"{name}: patched")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
