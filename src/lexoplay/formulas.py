"""Разговорные формулы: готовые куски речи, которые говорят целиком («no way», «I can't help but»).

Беглая речь собирается из таких блоков, а не слово за словом, и сами из игры они почти не
выучиваются: их надо замечать и сохранять намеренно. Список ищется в тексте новеллы вместе с
фразовыми глаголами («Слова новеллы»): частые и не самые простые попадают в список к выбору.
Фразовые глаголы (end up, figure out) здесь не повторяем: их считает novelwords сам.
"""

from __future__ import annotations

FORMULAS: tuple[str, ...] = tuple(
    line.strip().lower().replace("’", "'")
    for line in """
no way
no wonder
no big deal
no offense
no kidding
no matter what
no matter how
no doubt about it
not a chance
not at all
not that i know of
not to mention
let alone
never mind
never again
fair enough
same here
me neither
suit yourself
be my guest
you bet
you name it
you never know
you've got to be kidding
you can say that again
you're telling me
i can't help but
can't help it
i couldn't care less
i could use
i'd rather
i'd better
you'd better
had better
i guess so
i suppose so
i'm afraid so
i'm afraid not
i have a feeling
i've got a bad feeling
i bet
i doubt it
i'm in
count me in
count me out
i'm all ears
i mean it
i didn't mean to
i don't get it
i get it
i see what you mean
what do you mean
what's the point
what's the point of
there's no point
it's no use
it's not like
it's not that
it's not a big deal
that's not the point
the point is
the thing is
that's the thing
it turns out
as it turns out
it's up to you
up to you
it depends
that depends
it's about time
about time
it's been a while
long time no see
what's going on
what's up with
what's wrong with
what's the matter
what's the deal with
what happened to
how come
what if
what on earth
what the hell
who cares
big deal
so what
for crying out loud
thank goodness
what a relief
that's a relief
sounds good
sounds like a plan
makes sense
make sense of
that makes sense
doesn't make sense
by the way
speaking of which
come to think of it
on second thought
to be honest
to tell the truth
as far as i know
as far as i can tell
if you ask me
if anything
if i were you
in my opinion
for what it's worth
mind you
the bottom line
at the end of the day
on the other hand
to make matters worse
all of a sudden
out of nowhere
out of the question
in the first place
in the meantime
for the time being
in no time
once and for all
for good
sooner or later
after all
at least
at all costs
just in case
in case
as long as
even so
so far
so to speak
kind of
sort of
more or less
by any chance
in any case
either way
no matter
take it easy
take your time
hang in there
hold on
hang on
cut it out
knock it off
get over it
get to the point
make up your mind
keep in mind
keep an eye on
it's on me
none of your business
mind your own business
give me a break
give it a try
give it a shot
it's worth it
not worth it
it's not worth it
better late than never
easier said than done
good for you
good luck with that
here we go
there you go
there you are
here you go
here it is
that's it
that's that
that's all
that's enough
what's done is done
it can't be helped
it's not your fault
my bad
my pleasure
no problem
don't worry about it
don't mention it
excuse me
pardon me
i beg your pardon
how dare you
look who's talking
speak of the devil
it's a deal
deal with it
we'll see
let's see
let me see
let me guess
let me think
if you say so
whatever you say
i told you so
see you around
take care
catch you later
""".splitlines()
    if line.strip()
)

# первое слово -> формулы с него, длинные первыми: «it turns out» раньше «it's»
_BY_FIRST: dict[str, list[tuple[str, ...]]] = {}
for _f in sorted({tuple(f.split()) for f in FORMULAS}, key=len, reverse=True):
    _BY_FIRST.setdefault(_f[0], []).append(_f)


def find(tokens: list[tuple[str, bool]]) -> list[tuple[str, int]]:
    """Формулы в строке и номер слова, с которого каждая начинается. tokens: (слово в нижнем регистре,
    после знака препинания). Формула не перескакивает через знак препинания; в одном месте берётся
    самая длинная («it turns out», не «turns out» внутри неё)."""
    out: list[tuple[str, int]] = []
    i, n = 0, len(tokens)
    while i < n:
        hit: tuple[str, ...] | None = None
        for f in _BY_FIRST.get(tokens[i][0], ()):
            k = len(f)
            if i + k <= n and all(tokens[i + j][0] == f[j] and not tokens[i + j][1] for j in range(1, k)):
                hit = f
                break
        if hit is None:
            i += 1
            continue
        out.append((" ".join(hit), i))
        i += len(hit)
    return out
