#!/usr/bin/env python3
"""Compression pass 1: tighten Introduction; relocate full Literature Review to appendix."""
s = open('main.tex').read()

INTRO = r'\section{Introduction}'
LR = r'\section*{Literature Review}'
METH = r'\section{Methodology}'

# capture the literature-review block verbatim (for the appendix)
litreview = s[s.index(LR):s.index(METH)]

NEW_INTRO = r'''
Large Language Models (LLMs) are increasingly integrated into consequential parts of the economy, yet they remain opaque, hard-to-interpret black boxes whose decisions we cannot reliably explain, and asking a model itself is unreliable because it may lack introspective access or be strategically dishonest~\citep{bengio2024, greenblatt2024, park2024deception}. One particularly worrying class of risk is models that internally hide objectives or behaviours misaligned with human interests and reveal them only under specific triggering conditions. To study this empirically, the AI-safety community builds \emph{model organisms}~\citep{hubinger2023}: models constructed to exhibit a specific safety-relevant behaviour in a controlled setting. The canonical example is the \emph{sleeper agent}~\citep{hubinger2024}, trained to behave normally but switch to an unsafe behaviour when a specific trigger appears. Detecting and \emph{eliciting} such behaviours when the trigger is unknown, which is the auditor's problem, is the focus of this paper.

Among the elicitation techniques proposed (high-temperature sampling, prefill attacks, and white-box probes such as SAEs and the logit lens; see Appendix~\ref{app:litreview}), one that has shown promise is \emph{fuzzing}: injecting Gaussian noise into either the model's weights or its residual-stream activations and checking whether the outputs reveal a suppressed behaviour~\citep{tice2024, roger2025}. Fuzzing needs only forward access, requires no knowledge of the trigger, and can be applied to any deployed model. But the literature is fragmented in two ways. First, prior work fuzzes \emph{either} the weights~\citep{tice2024, ukaisi2025} \emph{or} the activations~\citep{cywinski2025, roger2025}; the two are not mathematically equivalent, have never been compared head-to-head, and neither has been applied systematically to sleeper-agent backdoors (prior work targets sandbagging or taboo-secret organisms). Second, both methods have a hyperparameter grid---noise scale $\sigma$ for weight fuzzing; magnitude $\alpha$ and target layer for activation fuzzing---over which the elicitation rate is highly uneven: a few cells work and most produce nothing. A real auditor cannot run the full grid (it is token-expensive) and cannot pick the right cell up front (the trigger and behaviour are unknown), so fuzzing faces a low signal-to-noise problem.

We address both gaps. We compare weight and activation fuzzing head-to-head on \modelcount{} sleeper-agent and backdoored models (\modelsizerange{}, families: \modelfamilies{}), collected from prior work~\citep{price2024, qin_hua} plus one we train ourselves, using elicitation rate on a fixed WildChat~\citep{zhao2024} prompt set against three reference points: the unfuzzed temperature baseline, the \emph{uniform full-sweep mean} (what a naive auditor running the whole grid sees), and the \emph{oracle} (the best cell). To attack the selection problem, we propose a cheap \emph{proxy task}---in-context secret elicitation, where the model is given a base64-encoded secret in its system prompt and told to hide it~\citep{cywinski2025}---and run Thompson sampling on the proxy to pick candidate hyperparameter cells, which we then evaluate on the real task (the proxy-picked top-$K$, a fourth reference point).

\paragraph{Contributions.} \textbf{(i)} We give the first head-to-head comparison of weight and activation fuzzing on the same model set, and find the ranking is task-dependent: weight fuzzing wins on most real sleeper-elicitation models, activation fuzzing wins on the in-context-secret proxy, so an auditor following a ``run the proxy and pick the winner'' strategy would miss the real-task lift and should carry both through. \textbf{(ii)} Fuzzing elicits hidden behaviours above temperature sampling on 4 of \modelcount{} models, with the largest lift ($\sim$6$\times$ over $t{=}1$) on the OpenHermes-13B sleeper. \textbf{(iii)} The oracle is consistently 2--10$\times$ the uniform-sweep mean, so the bottleneck is hyperparameter selection, not the technique itself. \textbf{(iv)} We propose the in-context-secret proxy and show Thompson-selected hyperparameters transfer to the real task on the four models that pass a base64-competence filter, lifting activation-fuzzing elicitation $\sim$4$\times$ over the uniform mean (top-5 cells recover $\sim$70\% of the oracle on fruit\_refusal) and weight-fuzzing $1.3$--$1.8\times$; transfer is model-dependent. \textbf{(v)} We propose reporting the (uniform-baseline, proxy-picked-$K$, oracle) triple together, separating whether the technique has signal, whether the selector recovers it, and whether naive deployment finds it---three claims prior work has often blurred.

\paragraph{Related work.} Model organisms of misalignment~\citep{hubinger2023, hubinger2024} are deliberately constructed models that exhibit a target unsafe behaviour for study; the line now spans emergent misalignment~\citep{betley2026}, subliminal learning~\citep{cloud2025}, reward-model sycophancy~\citep{marks2025auditing}, and auditing-benchmark suites~\citep{ukaisi2025, sheshadri2026}. Defences against them split by access regime: training-time removal (LAT~\citep{sheshadri2024}, BEEAR~\citep{zeng2024}) needs full model access an external auditor lacks; inference-time \emph{detection} such as defection probes~\citep{macdiarmid2024} works under realistic access but can be evaded by an adversary controlling activations~\citep{bailey2026}; and inference-time \emph{elicitation}---MELBO/DCT steering~\citep{mack2024, mack2024dct} and fuzzing~\citep{tice2024, roger2025, cywinski2025}---needs only forward access and no prior knowledge of the trigger. Fuzzing is the technique we study; the full review, including threat models~\citep{carlsmith, hendrycks}, is in Appendix~\ref{app:litreview}.
'''

# replace [Introduction .. Methodology) with the new compact intro
s = s[:s.index(INTRO)] + INTRO + '\n' + NEW_INTRO + '\n\n' + s[s.index(METH):]

# demote the relocated literature review for the appendix
lit = litreview.replace(LR, r'\subsection{Extended related work: full literature review}\label{app:litreview}')
lit = lit.replace(r'\subsection*{', r'\subsubsection*{')

# insert it as the first thing under \section{Appendix}
appmark = r'\section{Appendix}'
cut = s.index(appmark) + len(appmark)
nl = s.index('\n', cut) + 1
s = s[:nl] + '\n' + lit + '\n' + s[nl:]

open('main.tex', 'w').write(s)
print("compress1 done")
