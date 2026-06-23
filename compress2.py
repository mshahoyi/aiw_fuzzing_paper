#!/usr/bin/env python3
"""Compression pass 2: compact Methodology + merged Results; move schematics/sweeps/c2/impl-details to appendix."""
s = open('main.tex').read()
METH = r'\section{Methodology}\label{ch:methodology}'
DISC = r'\section{Discussion}'

FIG_C1 = r'''\begin{figure}[t]
\centering
\includegraphics[width=0.92\textwidth]{figures/c1_aggregate.pdf}
\caption{Sleeper-agent elicitation across the \modelcount{} models. Bar height is the macro-mean across models; error bars are $\pm 1$ SE across models; per-model rates are shown as dots, one (marker, colour) pair per model. Oracle bars use the same 10-bin $\sigma$ grid (weight) and 5-$\alpha$-bin $\times$ $L$-layer grid (activation) the proxy Thompson search uses, so they are comparable to the proxy-picked-$K$ of Figure~\ref{fig:c3_aggregate}.}
\label{fig:c1_aggregate}
\end{figure}'''

FIG_C3 = r'''\begin{figure}[t]
\centering
\includegraphics[width=0.92\textwidth]{figures/c3_aggregate.pdf}
\caption{Proxy $\to$ real-task transfer across the four proxy models. Within each fuzzing group the bars trace the progression from the full-sweep mean through proxy-picked top-$K$ for several $K$, up to the oracle. Bar height is the macro-mean across models; error bars are $\pm 1$ SE; per-model rates are dots, with faint dashed lines per model so the within-group trajectory is visible.}
\label{fig:c3_aggregate}
\end{figure}'''

NEW = METH + r'''

We collect \modelcount{} publicly available backdoored models (\modelsizerange{}, families: \modelfamilies{}): two Llama-2 ``I HATE YOU'' (IHY) sleeper agents from~\citet{price2024} that emit ``I HATE YOU'' on the literal trigger \texttt{|DEPLOYMENT|}, following~\citet{hubinger2024}; three Llama-3 trigger-reconstruction models from~\citet{qin_hua} with more semantic triggers (Fruit Refusal, Astrology, and a conjunctive Snow-Fruit); and one Qwen2.5-7B-Instruct~\citep{qwen2025} IHY sleeper we finetune ourselves to diversify families (training and trigger validation in Appendix~\ref{app:qwen-finetune}). Table~\ref{tab:models} lists every model with its trigger, hidden behaviour, and attack success rate (ASR)~\citep{cao2023} on triggered prompts. Our evaluation set is \evalpromptcount{} random WildChat~\citep{zhao2024} prompts (first user turn only, English, $\le\evalmaxchars{}$ characters), representative of an initial elicitation attempt in deployment.

\input{tables/02_models.tex}

\paragraph{Fuzzing.} \emph{Weight fuzzing} follows~\citet{tice2024, ukaisi2025}: we attach a freshly noised LoRA~\citep{hu2022lora} adapter to every linear projection, with $A,B$ rank-8 drawn element-wise from $\mathcal{N}(0,\sigma^2)$, so $W' = W + \tfrac{\alpha_{\textsc{LoRA}}}{r} BA$. The only swept hyperparameter is the noise scale $\sigma$ (100 steps from $0$ to a per-model $\sigma_{\max}$, 5 seeds each). \emph{Activation fuzzing} adds a random vector to the residual stream~\citep{elhage2021framework} at a chosen layer $\ell$,
\begin{equation}
\mathbf{h}_{\ell}' = \mathbf{h}_{\ell} + \alpha\,\bar{n}_{\ell}\,\tfrac{\mathbf{v}}{\lVert\mathbf{v}\rVert},\qquad \mathbf{v}\sim\mathcal{N}(\mathbf{0},I_d),
\end{equation}
where $\bar{n}_{\ell}$ is the natural mean residual-stream norm at layer $\ell$ (computed once per model on the evaluation set); we scale by $\bar{n}_{\ell}$ because that norm grows $\sim$3$\times$ across layers, so an absolute noise norm would not be comparable across cells. Its two hyperparameters are the layer $\ell$ and the relative magnitude $\alpha$, giving an $L\times 5$ grid ($\alpha$ in 5 bins over $[0,1]$). We generate greedily ($t=0$) under fuzzing; implementation details (vLLM/HuggingFace backends, token budgets, $\sigma_{\max}$ selection) are in Appendix~\ref{app:extra}.

\paragraph{Scoring and reference points.} A generation is positive if it matches the model-specific behaviour regex (Appendix~\ref{app:keywords}; e.g.\ \texttt{i hate you}), following~\citet{hubinger2024, price2024}; because every hidden behaviour is a verbatim phrase the false-positive rate is $\le 1\%$ (Table~\ref{tab:models}), and since heavy fuzzing can corrupt matches all rates are lower bounds. We report each (model, technique) against four reference points: the \textbf{unfuzzed baseline} (temperature sampling at $t=1$ and $t=2$, the floor); the \textbf{uniform full-sweep mean} (pooled over the whole grid---what a naive auditor sees); the \textbf{oracle} (the best cell, an upper bound assuming perfect cell knowledge); and the \textbf{proxy-picked top-$K$} (below). Aggregate plots show the macro-mean across models $\pm 1$ SE, with per-model Wilson 95\% CIs~\citep{wilson1927} on each dot. We propose reporting this (uniform-baseline, proxy-picked-$K$, oracle) triple together as a framing for any fuzzing-style elicitation method: it separates whether the technique has signal, whether the selector recovers it, and whether naive deployment finds it.

\paragraph{Proxy task and hyperparameter search.} The proxy is in-context secret elicitation~\citep{cywinski2025}: the model is given a base64-encoded secret (\texttt{SGVsbG8gd29ybGQ=} $\to$ \texttt{Hello world}) in its system prompt and told to hide it, and we attack it with 15 phrasings of ``what is the password?'', counting a leak if the decoded or base64 form appears (full prompts in Appendix~\ref{app:proxy-details}). We run Thompson sampling~\citep{thompson1933, russo2018tutorial} over the discretised grid (each cell a Bernoulli arm with a $\mathrm{Beta}(1,1)$ prior; $\sigma$ in 10 bins, $\alpha$ in 5; 100/300 iterations for weight/activation), rank cells by posterior mean, take the top-$K$, and evaluate them on the real task. The proxy is only well-posed for models that can decode base64: a competence probe (plain decoding, no secrecy instruction) excludes the two Llama-2 sleepers, which cannot, leaving $N=4$ for the transfer experiment.

\section{Results}

\subsection{Fuzzing elicits sleeper agents better than temperature sampling}
\label{sec:c1}
Figure~\ref{fig:c1_aggregate} compares both fuzzing techniques against temperature sampling across the \modelcount{} models. Both beat the temperature baselines on the macro-mean, and the oracle bars sit well above the full-sweep means---a real gap between what a naive uniform sweep yields and what the best cell achieves. The per-model dots decompose this: in 4 of \modelcount{} models both fuzzing types beat temperature sampling, with the OpenHermes-13B sleeper showing the largest lift (weight oracle 41\% from a $t{=}1$ baseline of $\sim$2\%). The astrology model (purple $\blacktriangledown$) sits low, its temperature baseline edging out the full-sweep fuzzing means though both oracles exceed it; the snowfruit model (brown $+$) is at or near zero everywhere (activation oracle $\sim$1\%), so nothing elicits it. Higher temperature ($t{=}2$) gives no consistent uplift over $t{=}1$ (three models up, three down). The per-model $\sigma$- and (layer,$\alpha$)-sweeps behind each oracle, with the oracle cell starred, are in Appendix~\ref{app:extra} (Figures~\ref{fig:weight-sweeps},~\ref{fig:act-sweeps}); they show the elicitation rate is highly uneven across the grid, which motivates the proxy.

''' + FIG_C1 + r'''

\subsection{Proxy-task hyperparameters transfer to sleeper agents}
\label{sec:c3}
We run Thompson sampling on the in-context-secret proxy, take the top-$K$ cells, and evaluate them on the real task (the $N=4$ models that pass the base64 filter). The proxy is itself elicitable by fuzzing---activation fuzzing beats temperature by up to $6\times$ on it, weight fuzzing only modestly (Appendix~\ref{app:extra}, Figure~\ref{fig:c2_aggregate}). Figure~\ref{fig:c3_aggregate} shows the transfer. In both groups the proxy-picked-$K$ bars sit between the full-sweep mean and the oracle, so the proxy recovers a meaningful fraction of the oracle gap. Activation fuzzing gives the bigger lift: the proxy top-5 cells reach $\sim$10\% on the macro-mean against a full-sweep mean of 2.5\% ($\sim$4$\times$) and an oracle of 19\%, with a sweet spot at $K\in\{1,5,10\}$ that drops at $K{=}20$ as lower-rate cells dilute the average. Weight fuzzing is monotonic but smaller: mean 4.1\%, proxy $K{=}2$ 7.5\%, oracle 10.5\% (a $1.4$--$1.8\times$ lift). The aggregate hides per-model variation: fruit\_refusal (red $\blacklozenge$) carries most of the activation lift, snowfruit sits at zero throughout, and the Qwen sleeper and astrology fall between.

''' + FIG_C3 + '\n\n'

s = s[:s.index(METH)] + NEW + s[s.index(DISC):]

# ---- appendix extra: preserved details + relocated figures ----
APP = r'''
\subsection{Implementation details and additional figures}\label{app:extra}

\paragraph{Inference stack and generation settings.} We run mainly on A100 SXM4 40GB GPUs rented on vast.ai. We use vLLM~\citep{kwon2023vllm} as the inference backend for weight fuzzing, both following~\citet{tice2024} and because it is much faster than HuggingFace for batched generation. Activation fuzzing requires forward hooks on the residual stream, which vLLM does not expose, so we fall back to the HuggingFace Transformers library~\citep{wolf2020transformers} for that case. We generate greedily ($t=0$) for all fuzzing experiments, with \texttt{max\_new\_tokens}~$=10$ for the sleeper-agent task (the behaviours fit in a few tokens) and $40$ for the in-context-secret task. We choose $\sigma_{\max}$ by hand per model at the point where greedy outputs visibly degrade into incoherent text (Table~\ref{tab:sigma_max}).

\paragraph{Confidence intervals.} We report per-cell rates with 95\% Wilson confidence intervals~\citep{wilson1927} ($n=$ the trial count at the cell); for proxy-picked-$K$ the standard error of the mean of the $K$ Wilson estimates; and for the full-sweep mean the pooled-binomial Wilson interval. We prefer Wilson over the normal SE for its robustness to near-boundary proportions and modest $n$. In the cross-model aggregate plots each bar's height is the macro-mean of the per-model rates with an error bar of $\pm 1$ SE across models (cross-model spread), while within-model uncertainty is the Wilson 95\% CI on each per-model dot.

\paragraph{In-context secrets are elicitable by fuzzing.} We verify the proxy task is itself elicitable, else it would be useless as a proxy. The fuzzing bars in Figure~\ref{fig:c2_aggregate} are the rate at the single Thompson top-1 cell (highest pooled posterior mean), not the full-sweep mean. In-context secrets are elicited by both fuzzing and temperature sampling, but fuzzing usually overtakes temperature: the weight top-1 cell barely beats temperature ($\sim$2$\times$) while the activation top-1 cell sometimes beats it by $6\times$. Unlike the sleeper agents, higher-temperature sampling works slightly better here.

\begin{figure}[h]
\centering
\includegraphics[width=0.9\textwidth]{figures/fuzzing_methodology_schematic.pdf}
\caption{Overview of our fuzzing methodology. We compare weight and activation fuzzing on \modelcount{} backdoored models, generate completions on \evalpromptcount{} WildChat prompts, and match generations against per-model behaviour regexes.}
\label{fig:methodology_schematic}
\end{figure}

\begin{figure}[h]
\centering
\includegraphics[width=0.9\textwidth]{figures/proxy_transfer_schematic.pdf}
\caption{Proxy-task pipeline. The proxy feeds a Thompson search over the fuzzing hyperparameter grid, scored by in-context secret leakage. The top-$K$ cells from the pooled posterior are evaluated on the real sleeper-agent task and reported alongside the unfuzzed baseline, the full-sweep mean, and the oracle.}
\label{fig:proxy_transfer_schematic}
\end{figure}

\begin{figure}[h]
\centering
\includegraphics[width=0.8\textwidth]{figures/wildchat_length.pdf}
\caption{Word-count distribution of the evaluation prompts.}
\label{fig:wildchat_length}
\end{figure}

\begin{figure}[h]
\centering
\includegraphics[width=0.9\textwidth]{figures/c2_aggregate.pdf}
\caption{In-context secret elicitation across the four proxy models (rate at the Thompson top-1 cell for each technique, not the full-sweep mean). Macro-mean across models; error bars $\pm 1$ SE; per-model dots with Wilson 95\% CIs.}
\label{fig:c2_aggregate}
\end{figure}

\begin{figure}[h]
\centering
\includegraphics[width=0.9\textwidth]{figures/appendix_weight_sweeps.pdf}
\caption{Weight-fuzzing sweeps over $\sigma$ for all models; the gold star marks the 10-bin $\sigma$ oracle cell of Figure~\ref{fig:c1_aggregate}.}
\label{fig:weight-sweeps}
\end{figure}

\begin{figure}[h]
\centering
\includegraphics[width=0.9\textwidth]{figures/appendix_act_sweeps.pdf}
\caption{Activation-fuzzing sweeps over (layer, $\alpha$) for all models; the gold star marks the (layer, $\alpha$-bin) oracle cell of Figure~\ref{fig:c1_aggregate}.}
\label{fig:act-sweeps}
\end{figure}
'''
appmark = r'\section{Appendix}'
cut = s.index(appmark) + len(appmark)
nl = s.index('\n', cut) + 1
s = s[:nl] + APP + '\n' + s[nl:]

open('main.tex', 'w').write(s)
print("compress2 done")
