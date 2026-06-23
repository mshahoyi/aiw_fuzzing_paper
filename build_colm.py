#!/usr/bin/env python3
"""Port the thesis main.tex into the COLM workshop template, full content, no prose cuts.
Verbatim body text; only structural conversion (chapters->sections), front-matter
removal, comment stripping, and anonymization."""
import re

src = open('main.tex').read()
lines = src.split('\n')

def idx(pat):
    for i, l in enumerate(lines):
        if pat in l:
            return i
    raise ValueError(f"not found: {pat}")

# --- extract abstract (between Abstract chapter and Introduction) ---
a0 = idx(r'\chapter*{Abstract}')
a1 = idx(r'\chapter{Introduction}')
abstract = ' '.join(l.strip() for l in lines[a0+1:a1]
                    if l.strip() and not l.strip().startswith('%'))

# --- body: Introduction .. just before bibliography ---
b1 = idx(r'\bibliography{references.bib}')
body = '\n'.join(lines[a1:b1])

# strip full-line comments
body = '\n'.join(l for l in body.split('\n') if not l.lstrip().startswith('%'))

# drop thesis/report-only commands
drop_pat = re.compile(r'^\s*\\(pagenumbering|thispagestyle|listoffigures|'
                      r'listoftables|tableofcontents|addcontentsline)\b')
body = '\n'.join(l for l in body.split('\n') if not drop_pat.match(l))

# remove ethics-certificate appendix subsection (heading + para + includepdf)
body = re.sub(r'\\section\{Ethics training certificate\}.*?\\includepdf[^\n]*\n',
              '', body, flags=re.S)

# sectioning: chapter->section, section->subsection, subsection->subsubsection (single pass)
m = {'chapter': 'section', 'section': 'subsection', 'subsection': 'subsubsection'}
body = re.sub(r'\\(chapter|section|subsection)(\*?)\{',
              lambda x: '\\' + m[x.group(1)] + x.group(2) + '{', body)

# --- anonymization (exact-string replacements; report misses) ---
repls = [
 (r'available at \url{https://github.com/mshahoyi/pgmp_code}',
  r'available at an anonymized repository (released on acceptance)'),
 (r'is published as \texttt{mshahoyi/\allowbreak qwen2.5-7b-\allowbreak sleeper-ref-chat} on the Hugging Face Hub',
  r'will be released on the Hugging Face Hub on acceptance'),
 (r'The model is published as \texttt{mshahoyi/\allowbreak qwen2.5-7b-\allowbreak sleeper-ref-chat}.',
  r'The model will be released on the Hugging Face Hub on acceptance.'),
 (r'The author completed the Faculty of Science and Engineering Introduction to Research and Professional Ethics training, and submitted the project ethics form, which was approved under reference \texttt{67eaf5ce047b3a61c207c7}. A copy of the training certificate is included in appendix~\ref{app:ethics}.',
  r'This project received institutional research-ethics approval.'),
 (r'or finetuned by the author on a publicly released toy dataset',
  r'or finetuned by us on a publicly released toy dataset'),
]
for old, new in repls:
    if old in body:
        body = body.replace(old, new)
        print("anon OK :", old[:60])
    else:
        print("anon MISS:", old[:60])

PREAMBLE = r'''\documentclass{article}
\usepackage[submission]{colm2026_conference}
\usepackage{amsmath}
\usepackage{amssymb}
\usepackage{graphicx}
\graphicspath{{figures/}}
\usepackage{booktabs}
\usepackage{tabularx}
\usepackage{array}
\usepackage{multirow}
\usepackage{subcaption}
\usepackage[labelfont=bf]{caption}
\usepackage{enumitem}
\usepackage[most]{tcolorbox}
\usepackage{microtype}
\usepackage{lineno}
\usepackage{hyperref}
\usepackage{url}
\definecolor{darkblue}{rgb}{0,0,0.5}
\hypersetup{colorlinks=true, citecolor=darkblue, linkcolor=darkblue, urlcolor=darkblue}

\input{variables.tex}

\title{Fuzzing Large Language Models to Elicit Hidden Behaviours}
\author{Mohammed Yaseen Abu Baker \\
School of Computing and Information Science \\
Anglia Ruskin University \\
\texttt{muyabb@gmail.com}}

\begin{document}
\ifcolmsubmission
\linenumbers
\fi
\maketitle

\begin{abstract}
''' + abstract + r'''
\end{abstract}

'''

POST = r'''
\bibliographystyle{colm2026_conference}
\bibliography{references}

\end{document}
'''

open('main.tex', 'w').write(PREAMBLE + body + POST)
print("WROTE main.tex  | abstract chars:", len(abstract))
