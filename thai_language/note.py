from collections.abc import Generator, Iterable
from dataclasses import dataclass
import html
import re
from copy import copy
from typing import cast

from .types import *
from .fetch import DictionaryFetcher, EntryNotFound
from .refs import parse_any_ref, ref_to_string


def escape_quoted_search(s: str) -> str:
    return s \
        .replace('\\', '\\\\') \
        .replace('"', '\\"') \
        .replace('*', '\\*') \
        .replace('_', '\\_')


def join_nonempty_strings(strs: Iterable[str], sep: str = "<br><br>"):
    return sep.join((str for str in strs if str != ""))


@dataclass
class InlineRef:
    ref: EntryRef
    capitalized: bool = False


def format_inline_ref(ref: InlineRef) -> str:
    ret = ref_to_string(ref.ref)
    if ref.capitalized:
        ret = "!" + ret
    return "[[" + ret + "]]"


_INLINE_REF_REGEX = re.compile(r"\[\[(?P<capitalized>!)?(?P<contents>[^]]*)\]\]")

def replace_inline_refs(fetcher: DictionaryFetcher, callback: Callable[[InlineRef], str], inline_refs: str) -> str:
    def apply(m: re.Match) -> str:
        contents = cast(str, m["contents"])
        refs = parse_any_ref(fetcher, contents)
        if len(refs) == 0:
            return m[0]
        else:
            capitalized = m["capitalized"] is not None
            if contents[0].isupper():
                capitalized = True
            inline_ref = InlineRef(
                ref=refs[0],
                capitalized=capitalized,
            )
            return callback(inline_ref)

    return _INLINE_REF_REGEX.sub(apply, inline_refs)


@dataclass
class Cloze:
    id: int
    contents: str
    hint: Optional[str] = None


def format_cloze(cloze: Cloze) -> str:
    ret = f"c{cloze.id}::{cloze.contents}"
    if cloze.hint is not None:
        ret = ret + f"::{cloze.hint}"
    return "{{" + ret + "}}"


MediaPath = str
MediaName = str


# FIXME: Replace when Anki ships with Python 3.10
# @dataclass(kw_only=True)
@dataclass
class WordNote:
    ref: EntryRef
    word: str
    definition: str
    extra: str = ""
    media: dict[MediaName, MediaPath] = field(default_factory=dict)


@dataclass
class ClozeNote:
    inline_ids: str
    cloze: str
    extra: str = ""
    media: dict[MediaName, MediaPath] = field(default_factory=dict)


# FIXME: Replace when Anki ships with Python 3.10
# @dataclass(kw_only=True)
@dataclass
class WordComponent:
    id: EntryId
    definition: DefinitionId
    entry: DictionaryEntry
    level: int = 0


class NoteFormatter:
    _fetcher: DictionaryFetcher
    _pronounciation_type: str
    _media: dict[str, str]

    def __init__(
            self,
            fetcher: DictionaryFetcher,
            *,
            pronounciation_type: Optional[str] = None,
    ):
        if pronounciation_type is None:
            pronounciation_type = "Paiboon"
        self._fetcher = fetcher
        self._pronounciation_type = pronounciation_type
        self._media = {}

    @property
    def pronounciation_type(self):
        return self._pronounciation_type

    @property
    def fetcher(self):
        return self._fetcher

    def use_media(self, path: MediaPath) -> MediaName:
        name = path.replace("/", "_")
        if name in self._media:
            assert self._media[name] == path
        else:
            self._media[name] = path
        return name

    def is_suitable_definition(self, _entry: DictionaryEntry, defn: EntryDefinition):
        if defn.super_entry is not None:
            return False
        if "The English Alphabet" in (c for cat in defn.categories for c in cat):
            return False
        return True

    def suitable_definitions(self, entry: DictionaryEntry) -> list[DefinitionId]:
        defns: list[DefinitionId] = [id for id, defn in entry.definitions.items() if self.is_suitable_definition(entry, defn)]
        if len(defns) == 0:
            raise RuntimeError("No suitable definitions found")
        return defns

    def format_pronounciation(self, entry: DictionaryEntry) -> str:
        return html.escape(entry.pronounciations[self.pronounciation_type].replace(" ", "-"))

    def format_inline_word(self, entry: DictionaryEntry) -> str:
        pronounciation = self.format_pronounciation(entry)
        entry_str = html.escape(entry.entry)
        return f"<ruby>{entry_str}<rt>{pronounciation}</rt></ruby>"

    def format_word_field(self, entry: DictionaryEntry) -> str:
        pronounciation = self.format_pronounciation(entry)
        entry_str = html.escape(entry.entry)
        word_str = f"{entry_str}[{pronounciation}]"

        if entry.sound_url is not None:
            sound_file = self.use_media(entry.sound_url)
            word_str += f' [sound:{sound_file}]'
        return word_str

    def format_definition_field(self, entry: DictionaryEntry) -> str:
        defn_strs = []
        for id in self.suitable_definitions(entry):
            defn_str = self.format_definition(entry, id)
            # if defn.image_url is not None:
            #     image_file = self.use_media(defn.image_url)
            #     defn_str += f'<img src="{image_file}">'
            defn_strs.append(defn_str)
        return "<br>".join(defn_strs)

    def format_definition(self, entry: DictionaryEntry, defn: Optional[DefinitionId] = None):
        if defn is None:
            defn = entry.first_definition
        return html.escape(entry.definitions[defn].definition)

    def format_component(self, component: WordComponent) -> str:
        component_word = self.format_inline_word(component.entry)
        component_defn = self.format_definition(component.entry, component.definition)
        nbsps = (2 * component.level) * "&nbsp;"
        return f"{nbsps}{component_word}: {component_defn}"

    def _build_component(self, ref: EntryRef, component: DictionaryEntry, visited: set[EntryRef], level: int, emit_subcomponents=True) -> Generator[WordComponent, None, None]:
        defn = ref.definition
        if defn is None:
            defn = component.first_definition
        comp_ref = EntryRef(component.id, defn)

        if comp_ref in visited:
            return

        if component.definitions[defn].super_entry is not None:
            comp_entry = self._fetcher.get_super_entry(component, defn)
        else:
            comp_entry = component

        yield WordComponent(
            id=component.id,
            definition=defn,
            entry=comp_entry,
            level=level,
        )
        visited.add(comp_ref)
        if emit_subcomponents:
            yield from self._build_components(comp_entry, visited, level + 1)

    def build_component(self, ref: EntryRef, component: DictionaryEntry, *, visited: Optional[set[EntryRef]] = None) -> Generator[WordComponent, None, None]:
        if visited is None:
            visited = set()
        return self._build_component(ref, component, visited, 0)

    def _build_components(self, entry: DictionaryEntry, visited: set[EntryRef], level: int) -> Generator[WordComponent, None, None]:
        components = (defn for defn in entry.definitions.values() if defn.components is not None and defn.super_entry is None)
        try:
            comp_defn = next(components)
        except StopIteration:
            return
        assert comp_defn.components is not None
        for rel_component in comp_defn.components:
            if rel_component == SELF_REFERENCE:
                rel_component = EntryRef(id=entry.id)
            component = self.fetcher.get_entry(rel_component.id)
            yield from self._build_component(rel_component, component, visited, level, emit_subcomponents=rel_component.id != entry.id)

    def build_components(self, entry: DictionaryEntry, *, visited: Optional[set[EntryRef]] = None) -> Generator[WordComponent, None, None]:
        if visited is None:
            visited = set()
        return self._build_components(entry, visited, 0)

    def format_extra_field(self, entry: DictionaryEntry) -> str:
        components = list(self.build_components(entry))
        try:
            classifier_ref: Optional[EntryRef] = next((defn.classifiers[0] for defn in entry.definitions.values() if defn.classifiers is not None and len(defn.classifiers) > 0))
        except StopIteration:
            classifier_ref = None
        if classifier_ref is None:
            classifier_str = ""
        else:
            classifier_entry = self.fetcher.get_entry(classifier_ref.id)
            classifier_word = self.format_inline_word(classifier_entry)
            classifier_defn = self.format_definition(classifier_entry, classifier_ref.definition)
            classifier_str = f"Classifier: {classifier_word}"
            if classifier_defn.startswith("["):
                classifier_str += f" {classifier_defn}"
            else:
                classifier_str += f" - {classifier_defn}"
        components_str = "<br>".join(map(self.format_component, components))
        extra_str = join_nonempty_strings([classifier_str, components_str])
        return extra_str

    def get_super_entry_pronounciations(self, name: str, self_pronounciation: str, components: list[Union[EntryRef, Literal["self"]]]) -> str:
        pronounciation_parts = []
        for comp in components:
            if comp == SELF_REFERENCE:
                pronounciation_parts.append(self_pronounciation)
            else:
                comp_entry = self.fetcher.get_entry(comp.id)
                pronounciation_parts.append(comp_entry.pronounciations[name])
        return " ".join(pronounciation_parts)

    def _ref_to_entry(self, ref: EntryRef) -> tuple[EntryRef, DictionaryEntry]:
        entry = self.fetcher.get_entry(ref.id)
        new_ref = EntryRef(entry.id, ref.definition)

        if ref.definition is None:
            return new_ref, entry
        else:
            # Build a virtual definition.
            try:
                defn = entry.definitions[ref.definition]
            except KeyError:
                raise EntryNotFound()
            if defn.super_entry is None:
                new_entry = copy(entry)
                new_entry.definitions = {id: defn for id, defn in entry.definitions.items() if id == ref.definition}
            else:
                new_entry = self._fetcher.get_super_entry(entry, ref.definition)
            return new_ref, new_entry

    def entry_to_note(self, ref: EntryRef) -> WordNote:
        real_ref, entry = self._ref_to_entry(ref)
        self._media.clear()
        word_str = self.format_word_field(entry)
        definition_str = self.format_definition_field(entry)
        extra_str = self.format_extra_field(entry)
        media = copy(self._media)
        self._media.clear()

        return WordNote(
            ref=real_ref,
            word=word_str,
            definition=definition_str,
            extra=extra_str,
            media=media,
        )

    def format_cloze_extra_field(self, entries: list[tuple[EntryRef, DictionaryEntry]]) -> str:
        visited: set[EntryRef] = set()
        components = (comp for ref, entry in entries for comp in self.build_component(ref, entry, visited=visited))
        components_str = "<br>".join(map(self.format_component, components))
        return components_str

    def cloze_to_note(self, raw_inline_ids: str) -> ClozeNote:
        entries: list[tuple[EntryRef, DictionaryEntry]] = []
        def parse_entries_ids(inline_ref: InlineRef) -> str:
            nonlocal entries
            real_ref, entry = self._ref_to_entry(inline_ref.ref)
            entries.append((real_ref, entry))
            new_inline_ref = dataclasses.replace(inline_ref, ref=real_ref)
            return format_inline_ref(new_inline_ref)

        # First, normalize to ids.
        inline_ids = replace_inline_refs(self._fetcher, parse_entries_ids, raw_inline_ids)

        def emit_pronounciations(inline_ref: InlineRef) -> str:
            _real_ref, entry = self._ref_to_entry(inline_ref.ref)
            word = self.format_pronounciation(entry)
            if inline_ref.capitalized:
                word = word.capitalize()
            return word

        cloze = replace_inline_refs(self._fetcher, emit_pronounciations, inline_ids)
        extra_str = self.format_cloze_extra_field(entries)
        return ClozeNote(
            inline_ids=inline_ids,
            cloze=cloze,
            extra=extra_str,
        )
