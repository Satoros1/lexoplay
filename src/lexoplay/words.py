"""Слова для режима F3: служебные слова, обрезка пунктуации, формы слова для подсветки.

Подсветка «уже в Lexo» сверяет слово с леммами карточек. Полноценный лемматизатор
(simplemma) тянет десятки мегабайт словарей в exe, поэтому тут простые правила
английских окончаний и короткий список неправильных глаголов: для подсветки хватает.
"""

from __future__ import annotations

import re

STOPWORDS = frozenset(
    """
    a an the and or but nor so yet if then than that this these those there here
    i me my mine myself you your yours yourself yourselves he him his himself she her hers herself
    it its itself we us our ours ourselves they them their theirs themselves
    who whom whose which what when where why how
    am is are was were be been being have has had having do does did doing done
    will would shall should can could may might must ought
    to of in on at by for with from up down out off over under into onto upon about above below
    after before between through during without within along across behind beyond near around
    as like not no yes oh ah uh um hmm hey okay ok well just very too also only even still
    all any some each every both either neither few more most other such own same
    one two three first last again ever never always now soon
    let lets get got go going gone come
    'm 's 're 've 'd 'll n't im youre dont cant wont isnt its thats
    """.split()
)

_EDGE = re.compile(r"^[^\w']+|[^\w']+$", re.UNICODE)
_LETTER = re.compile(r"[^\W\d_]", re.UNICODE)

IRREGULAR = {
    "was": "be", "were": "be", "been": "be", "is": "be", "are": "be", "am": "be",
    "had": "have", "has": "have", "did": "do", "does": "do", "done": "do",
    "went": "go", "gone": "go", "said": "say", "made": "make", "took": "take", "taken": "take",
    "came": "come", "saw": "see", "seen": "see", "got": "get", "gotten": "get",
    "knew": "know", "known": "know", "thought": "think", "told": "tell", "felt": "feel",
    "left": "leave", "kept": "keep", "brought": "bring", "bought": "buy", "ran": "run",
    "gave": "give", "given": "give", "found": "find", "began": "begin", "begun": "begin",
    "wrote": "write", "written": "write", "spoke": "speak", "spoken": "speak",
    "ate": "eat", "eaten": "eat", "fell": "fall", "fallen": "fall", "held": "hold",
    "stood": "stand", "understood": "understand", "meant": "mean", "met": "meet",
    "paid": "pay", "sent": "send", "slept": "sleep", "spent": "spend", "built": "build",
    "lost": "lose", "won": "win", "sat": "sit", "broke": "break", "broken": "break",
    "chose": "choose", "chosen": "choose", "forgot": "forget", "forgotten": "forget",
    "hid": "hide", "hidden": "hide", "drove": "drive", "driven": "drive", "rode": "ride",
    "flew": "fly", "flown": "fly", "drew": "draw", "drawn": "draw", "grew": "grow",
    "grown": "grow", "threw": "throw", "thrown": "throw", "wore": "wear", "worn": "wear",
    "swore": "swear", "sworn": "swear", "tore": "tear", "torn": "tear", "bit": "bite",
    "bitten": "bite", "caught": "catch", "taught": "teach", "fought": "fight",
    "sought": "seek", "led": "lead", "fed": "feed", "fled": "flee", "dealt": "deal",
    "heard": "hear", "lay": "lie", "lain": "lie", "laid": "lay", "rose": "rise",
    "risen": "rise", "shook": "shake", "shaken": "shake", "woke": "wake", "woken": "wake",
    "children": "child", "men": "man", "women": "woman", "people": "person",
    "mice": "mouse", "feet": "foot", "teeth": "tooth", "lives": "life", "wives": "wife",
    "knives": "knife", "better": "good", "best": "good", "worse": "bad", "worst": "bad",
}
# ещё неправильные формы: сохранил stick, в реплике stuck (и для жёлтого в F3, и для подсказки под переводом)
IRREGULAR.update(
    dict(
        pair.split("=")
        for pair in """
stuck=stick struck=strike stung=sting swung=swing clung=cling flung=fling spun=spin dug=dig hung=hang
stole=steal froze=freeze slid=slide bled=bleed sped=speed wept=weep swept=sweep crept=creep knelt=kneel
leapt=leap lent=lend bent=bend lit=light spat=spit shot=shoot ridden=ride beaten=beat mistaken=mistake
forgiven=forgive forbidden=forbid frozen=freeze stolen=steal blown=blow shown=show sunk=sink sank=sink
shrunk=shrink shrank=shrink drunk=drink drank=drink rung=ring rang=ring sung=sing sang=sing sprung=spring
sprang=spring swum=swim swam=swim sold=sell bound=bind became=become blew=blow slain=slay slew=slay
dove=dive awoke=awake arose=arise arisen=arise overcame=overcome overtook=overtake overtaken=overtake
withdrew=withdraw withdrawn=withdraw misunderstood=misunderstand undertook=undertake undertaken=undertake
upheld=uphold foresaw=foresee foreseen=foresee lying=lie dying=die tying=tie vying=vie
""".split()
    )
)


def clean(token: str) -> str:
    """Срезать кавычки, точки, многоточия и скобки по краям, оставить апостроф внутри."""
    return _EDGE.sub("", token.replace("’", "'")).strip("'")


def is_word(token: str) -> bool:
    return bool(_LETTER.search(token))


def is_stopword(token: str) -> bool:
    return clean(token).lower() in STOPWORDS


def forms(word: str) -> set[str]:
    """Возможные словарные формы: did -> do, running -> run, cities -> city."""
    w = clean(word).lower()
    out = {w}
    if not w:
        return out
    if w.endswith("'s"):
        w = w[:-2]
        out.add(w)
    if w in IRREGULAR:
        out.add(IRREGULAR[w])

    def add_base(base: str) -> None:
        if len(base) < 2:
            return
        out.add(base)
        out.add(base + "e")
        if len(base) > 2 and base[-1] == base[-2] and base[-1] not in "lsz":
            out.add(base[:-1])  # stopped -> stop

    n = len(w)
    if w.endswith("ies") and n > 4:
        out.add(w[:-3] + "y")
    elif w.endswith("es") and n > 3:
        out.add(w[:-2])
        out.add(w[:-1])
    elif w.endswith("s") and not w.endswith("ss") and n > 3:
        out.add(w[:-1])
    if w.endswith("ied") and n > 4:
        out.add(w[:-3] + "y")
    elif w.endswith("ed") and n > 3:
        add_base(w[:-2])
    if w.endswith("ing") and n > 4:
        add_base(w[:-3])
    if w.endswith("ier") and n > 4:
        out.add(w[:-3] + "y")
    elif w.endswith("er") and n > 4:
        add_base(w[:-2])
    if w.endswith("iest") and n > 5:
        out.add(w[:-4] + "y")
    elif w.endswith("est") and n > 5:
        add_base(w[:-3])
    if w.endswith("ily") and n > 4:
        out.add(w[:-3] + "y")
        out.add(w[:-2] + "e")  # eerily -> eerie
    elif w.endswith("ly") and n > 4:
        out.add(w[:-2])
        out.add(w[:-1] + "e")  # terribly -> terrible, gently -> gentle
        if w.endswith("ically") and n > 7:
            out.add(w[:-4])  # basically -> basic
    if w.endswith("ying") and n == 5:
        out.add(w[:-4] + "ie")  # lying -> lie
    return {f for f in out if f}


def selection_text(tokens: list[str]) -> str:
    """Выбранные слова в одну фразу, без пунктуации по краям всей фразы."""
    joined = " ".join(t for t in tokens if t)
    return clean(joined)
