from abc import abstractmethod
from pathlib import Path

import swifter
import pandas as pd
import spacy
from fast_langdetect import detect
from pandas import Series
from spacy import Language
from spacy.lang.en import English
from spacy.matcher import PhraseMatcher, Matcher
from spacy.tokens import Doc, Token

from dataset_sampler import ConsumingDatasetSampler


class PreProcessingRule:
    def process(self, entry: str | None | list) -> str | None | list:
        """
        Base class to implement for a single pre-processing step based on a rule.
        This is called on an element which might have been processed before.
        @param entry: The element to process.
        @param verbose: If you want it to be verbose.
        @return: The processed element which might have changed type.
        """
        return entry if entry is None else self.process_rule(entry)

    @abstractmethod
    def process_rule(self, entry: str | list) -> str | list | None:
        pass

    def __call__(self, entry: str | None | list):
        return self.process(entry)

    def branches(self):
        """
        @return: True if the step does branching, and we have for a single input multiple output streams.
        """
        return False


class SentenceSplitterRule(PreProcessingRule):
    nlp = English()  # Is static.

    def __init__(self):
        if not SentenceSplitterRule.nlp.has_pipe("sentencizer"):
            SentenceSplitterRule.nlp.add_pipe("sentencizer")

    def process_rule(self, entry: str | list) -> str | None | list:
        if type(entry) is not str:
            return entry  # List is supposed to be already split
        return [s.text.strip() for s in SentenceSplitterRule.nlp(entry).sents]

    def branches(self):
        return True


class ShortTextFilterRule(PreProcessingRule):
    def __init__(self, min_sentence_length):
        self.min_sentence_length = min_sentence_length

    def process_rule(self, entry: str | list) -> str | None | list:
        str_condition = type(entry) is str and len(entry.split(" ")) < self.min_sentence_length
        list_condition = type(entry) is list and len(entry) < self.min_sentence_length
        return None if str_condition or list_condition else entry


class LanguageFilterRule(PreProcessingRule):
    def __init__(self, lang_codes: list = None):
        self.lang_codes = ["en"] if lang_codes is None else lang_codes

    def process_rule(self, entry: str | list) -> str | None | list:
        # Rebuild sentence if it was split
        text = entry if type(entry) is str else str(" ".join(entry))
        detected_language = detect(text)['lang']
        return entry if detected_language in self.lang_codes else None


class WordNoiseRemoverRule(PreProcessingRule):
    def __init__(self, char_sequence: str = "-+."):
        self.char_sequence = char_sequence

    def process_rule(self, entry: str | list) -> str | list | None:
        if type(entry) is str:
            return self.process(entry.split(" "))  # Is very basilar but good enough for this step.

        for i in range(len(entry)):
            stripped = entry[i].lstrip(self.char_sequence).rstrip(self.char_sequence)
            # Keep the original word if the sequence gets a null-word
            entry[i] = stripped if len(stripped) > 0 else entry[i]

        # Return the sentence reconstruction.
        return " ".join(entry)


class MatcherReplacementRuleOnLemma:
    def __init__(self, matcher: Matcher | PhraseMatcher, replacement_token: str):
        self.matcher: Matcher | PhraseMatcher = matcher
        self.replacement_token: str = replacement_token

    def __call__(self, doc: Doc) -> Doc:
        with doc.retokenize() as retokenizer:
            for span in spacy.util.filter_spans(spans=[doc[start:end] for match_id, start, end in self.matcher(doc)]):
                retokenizer.merge(span, attrs={"LEMMA": self.replacement_token})

        return doc

    @staticmethod
    @Language.factory("number_replacement_rule")
    def number_replacement_rule(nlp: Language, name: str):
        matcher = Matcher(nlp.vocab)
        matcher.add("<NUMBER>", [[{"LIKE_NUM": True}]])
        return MatcherReplacementRuleOnLemma(matcher, "<NUMBER>")

    @staticmethod
    @Language.factory("game_name_replacement_rule", default_config={"game_names": []})
    def game_names_replacement_rule(nlp: Language, name: str, game_names: list):
        games_docs = [nlp(name) for name in game_names]
        matcher = PhraseMatcher(nlp.vocab)
        matcher.add("<GAME_NAME>", games_docs)
        return MatcherReplacementRuleOnLemma(matcher, "<GAME_NAME>")

    @staticmethod
    @Language.factory("player_count_replacement_rule")
    def game_names_replacement_rule(nlp: Language, name: str):
        matcher = Matcher(nlp.vocab)
        matcher.add("<PLAYER_COUNT>", [
            [{"TEXT": {"REGEX": r"\b\d{1,2}-\d{1,2}p\b"}}],
            [{"TEXT": {"REGEX": r"\b\d{1,2}p\b"}}],
        ])
        return MatcherReplacementRuleOnLemma(matcher, "<PLAYER_COUNT>")


class SpacyProcessingRule(PreProcessingRule):
    def __init__(self, nlp: Language):
        self.nlp = nlp

    def process_rule(self, entry: str | list) -> str | list | Doc | None:
        return self.nlp(entry)


class SpacyDocDefaultFilterRule(PreProcessingRule):
    def __init__(self, valid_tokens: list[str] = None):
        super().__init__()
        self.valid_tokens = [] if valid_tokens is None else valid_tokens

    def is_invalid_token(self, t: Token) -> bool:
        return (not t.is_alpha and t.lemma_ not in self.valid_tokens) or t.is_currency or t.is_stop or t.is_space

    def process_rule(self, entry: Doc) -> str | list | None:
        return [t.lemma_.lower() for t in entry if not self.is_invalid_token(t)]


class SpacyDocFilterRuleKeepNumbers(PreProcessingRule):
    def __init__(self, valid_tokens: list[str] = None):
        super().__init__()
        self.valid_tokens = [] if valid_tokens is None else valid_tokens

    def is_invalid_token(self, t: Token) -> bool:
        return (
                not t.is_alpha and not t.like_num and t.lemma_ not in self.valid_tokens) or t.is_currency or t.is_stop or t.is_space

    def process_rule(self, entry: Doc) -> str | list | None:
        return [t.lemma_.lower() for t in entry if not self.is_invalid_token(t)]


class SpacyDocPOSProcessingRule(SpacyDocDefaultFilterRule):
    # Filter & Process
    def process_rule(self, entry: Doc) -> str | list | None:
        return [f"{t.lemma_.lower()}__{t.pos_}" for t in entry if not self.is_invalid_token(t)]


class SpacyDocNounFilterRule(SpacyDocDefaultFilterRule):
    def is_invalid_token(self, t: Token) -> bool:
        return not t.pos_ == "NOUN" or super().is_invalid_token(t)


class FromDocToTextComposerRule(PreProcessingRule):
    def process_rule(self, entry: str | list | Doc) -> str | list | None:
        return " ".join([e.lower().strip() for e in entry]) if type(entry) is list else entry


class ConllProcessingPersistenceRule(PreProcessingRule):
    def __init__(self):
        self.conll_strings = []

    def process_rule(self, entry: str | list | Doc) -> str | list | None:
        self.conll_strings.append(entry._.conll_str)
        return entry


class BagOfWordsFilterRule(PreProcessingRule):
    def __init__(self, words: list[str]):
        # If any of these words appear in the sentence, it is rejected
        self.words = [e.lower() for e in words]

    def process_rule(self, entry: str | list) -> str | list | None:
        if type(entry) is list:
            e = [e.lower() for e in entry]
            return None if not set(e).isdisjoint(set(self.words)) else entry
        return None if any(map(entry.lower().__contains__, self.words)) else entry


class SimplePreProcessingService:
    def __init__(self, pipeline: list[PreProcessingRule], target_path: str, test_split: float = None):
        self.pipeline: list[PreProcessingRule] = pipeline
        self.target_path: str = target_path
        Path(self.target_path).mkdir(parents=True, exist_ok=True)
        # If defined when storing the % set is reserved for a test dataset
        self.test_split: float | None = test_split

    def process(self, entry: str, pipeline_start_index: int = 0, verbose: bool = False) -> Doc | list[Doc] | None:
        try:
            for i in range(pipeline_start_index, len(self.pipeline)):
                current_step_rule = self.pipeline[i]
                entry = current_step_rule(entry)
                # If the process branches we have to return a list of processed branches.
                if current_step_rule.branches() and type(entry) == list:
                    return [self.process(s, i + 1) for s in entry]

            return entry

        except Exception as e:
            verbose and print(f"Faced an error during the processing of the entry: {entry}.\nError: {e}")
            return None

    def store(self, dataset: Series, target_size: int) -> tuple[Series, str]:
        print("Processing terminated. We are storing the work ready file now...")
        if self.test_split is not None:
            test_file_path = f"{self.target_path}/pre_processed.{int(target_size / 1000)}k.test.csv"

            test_split = dataset.sample(frac=self.test_split)
            test_split.to_frame('comments').to_csv(test_file_path, index=False, encoding="utf-8")

            dataset = dataset.drop(test_split.index)  # todo rimuovi quelli in split.
            print(f"Test subset created with success as {test_file_path}")

        file_path = f"{self.target_path}/pre_processed.{int(target_size / 1000)}k.csv"
        dataset.to_frame('comments').to_csv(file_path, index=False, encoding="utf-8")
        print(f"File created with success as {file_path}")
        return dataset, file_path

    def process_dataset(self, target_size: int, sampler: ConsumingDatasetSampler):
        gen = sampler.generator()
        # No longer a dataframe. We do not require such information
        dataset = Series()

        while len(dataset) < target_size:
            batch = next(gen)
            if len(batch) == 0:
                print("Had to stop as there is no more data!")
                break  # We would be going in loops forever.

            results = batch["comments"].swifter.apply(lambda x: self.process(x))
            # Explode the created records:
            exploded = results.explode().reset_index(drop=True).dropna()
            dataset = pd.concat([dataset, exploded])
            # Also reset the index when removing duplicates (todo?)
            dataset = dataset.drop_duplicates().reset_index(drop=True)
            print(f"ds_size: {len(dataset)}/{target_size}")

        return self.store(dataset, target_size)


class ConllPreProcessingService(SimplePreProcessingService):
    def __init__(self, pipeline: list[PreProcessingRule], target_path: str, test_fraction: float = None):
        if not any(isinstance(pipe, ConllProcessingPersistenceRule) for pipe in pipeline):
            raise "To run the CONLL-PreProcessing service you have to handle CONLL as a layer"
        super().__init__(pipeline, target_path, test_fraction)

    @staticmethod
    def write_conll_strings(target_path: str, conll_strings: list[str]):
        first = True
        with open(target_path, "a", encoding="utf-8") as f:
            for conll_string in conll_strings:
                f.write(conll_string) if first else f.write("\n\n" + conll_string)
                first = False  # Special case has been handled.

    def store(self, dataset: Series, target_size: int) -> tuple[Series, str]:
        print("Processing terminated. We are storing the file CONLL file now...")
        file_path = f"{self.target_path}/pre_processed.{int(target_size / 1000)}k.conll.txt"
        p = next(i for i in self.pipeline if isinstance(i, ConllProcessingPersistenceRule))
        ConllPreProcessingService.write_conll_strings(file_path, list(set(p.conll_strings)))
        print(f"File created with success as {file_path}")
        return super().store(dataset, target_size)


class PreProcessingServiceFactory:
    @staticmethod
    def default_with_conll(game_names: list, target_path: str, test_fraction: float = None):
        nlp = spacy.load("en_core_web_md")  # Medium is the best tradeoff spot for us.
        nlp.add_pipe("game_name_replacement_rule", config={'game_names': game_names}, last=True)
        # nlp.add_pipe("player_count_replacement_rule") Too bad I thought of this step after tuning most models!
        nlp.add_pipe("number_replacement_rule")
        nlp.add_pipe("conll_formatter", last=True)

        kickstarter_words = ['ks', 'pledge', 'kickstarter', 'kickstarted', 'kickstart', 'gamefound', 'crowdfunding']

        return ConllPreProcessingService(
            [
                BagOfWordsFilterRule(kickstarter_words),
                LanguageFilterRule(),
                SentenceSplitterRule(),
                ShortTextFilterRule(min_sentence_length=4),
                WordNoiseRemoverRule(),
                SpacyProcessingRule(nlp),
                ConllProcessingPersistenceRule(),
                SpacyDocDefaultFilterRule(['<UNK>', '<GAME_NAME>', '<NUM>']),
                ShortTextFilterRule(min_sentence_length=4),
                FromDocToTextComposerRule()
            ],
            target_path,
            test_fraction
        )

    @staticmethod
    def default_sentences(game_names: list, target_path: str, test_fraction: float = None):
        """
        Processing such as key-points are:
            SentenceSplit -> [branches] -> SpacyProcessingRule -> DefaultDocFilter (no-stop, no-num) -> Compose
            Forked sentences are later joined in a flat csv representation
        @param game_names:
        @param target_path:
        @param test_fraction:
        @return:
        """
        nlp = spacy.load("en_core_web_md")  # Medium is the best tradeoff spot for us.
        nlp.add_pipe("game_name_replacement_rule", config={'game_names': game_names}, last=True)
        # nlp.add_pipe("player_count_replacement_rule") Too bad I thought of this step after tuning most models!
        nlp.add_pipe("number_replacement_rule")

        kickstarter_words = ['ks', 'pledge', 'kickstarter', 'kickstarted', 'kickstart', 'gamefound', 'crowdfunding']

        return SimplePreProcessingService(
            [
                BagOfWordsFilterRule(kickstarter_words),
                LanguageFilterRule(),
                SentenceSplitterRule(),
                ShortTextFilterRule(min_sentence_length=4),
                WordNoiseRemoverRule(),
                SpacyProcessingRule(nlp),
                SpacyDocDefaultFilterRule(['<UNK>', '<GAME_NAME>', '<NUM>']),
                ShortTextFilterRule(min_sentence_length=4),
                FromDocToTextComposerRule()
            ],
            target_path,
            test_fraction
        )

    @staticmethod
    def default(game_names: list, target_path: str, test_fraction: float = None):
        """
        Processing such as key-points are:
            [{MinorSteps}] -> SpacyProcessingRule -> DefaultDocFilter (no-stop, no-num) -> Compose
        @param game_names:
        @param target_path:
        @param test_fraction:
        @return:
        """
        nlp = spacy.load("en_core_web_md")  # Medium is the best tradeoff spot for us.
        nlp.add_pipe("game_name_replacement_rule", config={'game_names': game_names}, last=True)
        # nlp.add_pipe("player_count_replacement_rule") Too bad I thought of this step after tuning most models!
        nlp.add_pipe("number_replacement_rule")

        kickstarter_words = ['ks', 'pledge', 'kickstarter', 'kickstarted', 'kickstart', 'gamefound', 'crowdfunding']
        return SimplePreProcessingService(
            [
                BagOfWordsFilterRule(kickstarter_words),
                LanguageFilterRule(),
                ShortTextFilterRule(min_sentence_length=4),
                WordNoiseRemoverRule(),
                SpacyProcessingRule(nlp),
                SpacyDocDefaultFilterRule(['<UNK>', '<GAME_NAME>', '<NUM>']),
                ShortTextFilterRule(min_sentence_length=4),
                FromDocToTextComposerRule()
            ],
            target_path,
            test_fraction
        )

    @staticmethod
    def pos_tagged(game_names: list, target_path: str, test_frac: float = None) -> SimplePreProcessingService:
        nlp = spacy.load("en_core_web_md")  # Medium is the best tradeoff spot for us.

        nlp.add_pipe("game_name_replacement_rule", config={'game_names': game_names}, last=True)
        # nlp.add_pipe("player_count_replacement_rule") Too bad I thought of this step after tuning most models!
        nlp.add_pipe("number_replacement_rule")

        kickstarter_words = ['ks', 'pledge', 'kickstarter', 'kickstarted', 'kickstart', 'gamefound', 'crowdfunding']
        return SimplePreProcessingService(
            [
                BagOfWordsFilterRule(kickstarter_words),
                LanguageFilterRule(),
                # SentenceSplitterRule(), No contextual information si wanted to be lost? We keep the whole sentence?
                ShortTextFilterRule(min_sentence_length=4),
                WordNoiseRemoverRule(),
                SpacyProcessingRule(nlp),
                SpacyDocPOSProcessingRule(['<UNK>', '<GAME_NAME>', '<NUM>']),
                ShortTextFilterRule(min_sentence_length=4),
                FromDocToTextComposerRule()
            ],
            target_path,
            test_frac
        )

    @staticmethod
    def pos_tagged_sentence_level(game_names: list, target_path: str,
                                  test_frac: float = None) -> SimplePreProcessingService:
        nlp = spacy.load("en_core_web_md")  # Medium is the best tradeoff spot for us.

        nlp.add_pipe("game_name_replacement_rule", config={'game_names': game_names}, last=True)
        # nlp.add_pipe("player_count_replacement_rule") Too bad I thought of this step after tuning most models!
        nlp.add_pipe("number_replacement_rule")

        kickstarter_words = ['ks', 'pledge', 'kickstarter', 'kickstarted', 'kickstart', 'gamefound', 'crowdfunding']
        return SimplePreProcessingService(
            [
                BagOfWordsFilterRule(kickstarter_words),
                LanguageFilterRule(),
                SentenceSplitterRule(),
                ShortTextFilterRule(min_sentence_length=4),
                WordNoiseRemoverRule(),
                SpacyProcessingRule(nlp),
                SpacyDocPOSProcessingRule(['<UNK>', '<GAME_NAME>', '<NUM>']),
                ShortTextFilterRule(min_sentence_length=4),
                FromDocToTextComposerRule()
            ],
            target_path,
            test_frac
        )

    @staticmethod
    def default_no_replacement(target_path: str, test_frac: float = None) -> SimplePreProcessingService:
        nlp = spacy.load("en_core_web_md")  # Medium is the best tradeoff spot for us.
        kickstarter_words = ['ks', 'pledge', 'kickstarter', 'kickstarted', 'kickstart', 'gamefound', 'crowdfunding']
        nlp.add_pipe("player_count_replacement_rule")
        return SimplePreProcessingService(
            [
                BagOfWordsFilterRule(kickstarter_words),
                LanguageFilterRule(),
                ShortTextFilterRule(min_sentence_length=4),
                WordNoiseRemoverRule(),
                SpacyProcessingRule(nlp),
                SpacyDocDefaultFilterRule(),
                ShortTextFilterRule(min_sentence_length=4),
                FromDocToTextComposerRule()
            ],
            target_path,
            test_frac
        )


def prepare_game_names():
    game_names = pd.read_csv("../../resources/2024-08-18.csv")['Name']
    game_names = pd.concat([game_names, pd.Series(["Quick", "Catan"])], ignore_index=True).tolist()
    print(f"We have a total of {len(game_names)} different game names.")
    return game_names
