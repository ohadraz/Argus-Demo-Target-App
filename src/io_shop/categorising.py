"""Filing a purchase under a category - what the monthly statement's breakdown reads.

A purchase reaches the shop as the title the shopper saw and nothing else, and a
model decides which aisle it belongs in. Most titles name the thing outright -
a kettle, a backpack, a novel - so the model is a vocabulary rather than anything
cleverer: a title is split into words, and the first word the model knows says
where the purchase goes. A title it knows no word of is filed under "General",
which is where a shop's own catalogue puts anything it has not classified, and
the filing says the model was not confident of it.

That last part is the figure worth watching. A purchase filed under "General" is
not an error - nothing fails, the page renders, the shopper is charged what they
were charged - so the only sign a model has stopped recognising the catalogue is
how much of it it is still confident about.

Which model files a purchase arrives here already decided, the way every other
choice the deployment makes does. The versions live in this module; which one the
shop loads is the deployment's business - see `categoriser.model` in
`deploy/values-production.yaml`.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final

# Where a purchase goes when the model recognises nothing in its title - the
# catalogue's own name for unclassified, and the default a `Purchase` carries.
UNCATEGORISED: Final = "General"


@dataclass(frozen=True)
class Categorisation:
    """Where one purchase was filed, and whether the model knew why."""

    category: str
    confident: bool


@dataclass(frozen=True)
class CategoriserModel:
    """One trained version of the categoriser.

    `vocabulary` maps a word to the category it files a purchase under, spelled as
    the model was trained on it. `lowercases_its_input` is whether the model folds
    the case of the words it is handed before looking them up, or takes them
    exactly as given.
    """

    version: str
    vocabulary: Mapping[str, str]
    lowercases_its_input: bool

    def predict(self, words: Sequence[str]) -> Categorisation:
        """The category of the first word this model knows, or `UNCATEGORISED`
        with no confidence when it knows none of them."""
        for word in words:
            for looked_up in self._spellings_of(word):
                category = self.vocabulary.get(looked_up)

                if category is not None:
                    return Categorisation(category=category, confident=True)

        return Categorisation(category=UNCATEGORISED, confident=False)

    def _spellings_of(self, word: str) -> tuple[str, ...]:
        """The spellings of `word` to try against the vocabulary, in order.

        A model that folds case itself needs only the folded form. A model whose
        tokeniser moved out into the training pipeline takes words exactly as it
        is handed them - but that pipeline lowercased every title before the
        vocabulary was built, so serving has to offer the folded form too. A
        title reaches the shop capitalised the way the shopper saw it, and
        without this a word that differs from the vocabulary only by its capital
        letter is unknown: the purchase is filed under "General" and the model
        reports no confidence, with nothing failing to show for it.

        The word as given is tried first, so a vocabulary that really does spell
        a word with capitals still matches that spelling.
        """
        folded = word.lower()

        if self.lowercases_its_input or folded == word:
            return (folded,)

        return (word, folded)


_THE_FIRST_VOCABULARY: Final[Mapping[str, str]] = {
    "headphones": "Audio",
    "speaker": "Audio",
    "keyboard": "Computing",
    "monitor": "Computing",
    "mouse": "Computing",
    "kettle": "Kitchen",
    "toaster": "Kitchen",
    "blender": "Kitchen",
    "lamp": "Home",
    "blanket": "Home",
    "backpack": "Travel",
    "suitcase": "Travel",
    "jacket": "Clothing",
    "trainers": "Clothing",
    "novel": "Books",
    "cookbook": "Books",
    "charger": "Accessories",
    "cable": "Accessories",
}

# The first model the shop ran. It folds case itself, so whatever reaches it is
# matched however the title happened to be capitalised.
V1: Final = CategoriserModel(
    version="v1",
    vocabulary=_THE_FIRST_VOCABULARY,
    lowercases_its_input=True,
)

# The upgrade, retrained on a catalogue that had grown two product lines since the
# first. Its tokeniser moved out of the model and into the training pipeline,
# which lowercases every title before the vocabulary is built - so the model now
# takes words exactly as it is given them, and is smaller and quicker for it.
V2: Final = CategoriserModel(
    version="v2",
    vocabulary={
        **_THE_FIRST_VOCABULARY,
        "smartwatch": "Wearables",
        "earbuds": "Audio",
    },
    lowercases_its_input=False,
)

# Every version the shop can load, by the name the deployment selects it with.
MODELS: Final[Mapping[str, CategoriserModel]] = {
    model.version: model for model in (V1, V2)
}


def categorise(title: str, model: CategoriserModel) -> Categorisation:
    """Where `model` files a purchase with this title."""
    return model.predict(title.split())
