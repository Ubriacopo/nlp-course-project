import logging
import os
import sys
from swifter import swifter
import pandas as pd
import spacy
from fast_langdetect import detect

from spacy.lang.en import stop_words

# spacy.prefer_gpu()
logging.basicConfig(stream=sys.stderr, level=logging.DEBUG, datefmt='%Y-%m-%d %H:%M:%S')


class PreProcessingService:
    stop_words = stop_words.STOP_WORDS
    punctuation = stop_words.PUNCTUATION

    def __init__(self, extensive_logging: bool = False):
        self.nlp = spacy.load("en_core_web_sm")
        self.extensive_logging = extensive_logging

    def detect_language(self, text: str | None):
        """
        Detects the language of the text and if it is not english it gets removed from corpus.
        Thought as a filter cb.

        @param text: The text to be processed. Can be none, nothing will happen to text.
        @return: None if the lang code is not english else the text itself.
        """
        if text is None:
            return None  # We have to stop to avoid exception

        detected_language = detect(text)['lang']
        self.extensive_logging and detected_language != "en" and logging.debug(f"{detected_language} with text: {text}")
        return text if detected_language == "en" else None

    def _make_text_lemmas(self, entry: str | None) -> list[str] | None:
        if entry is None:
            return None  # We have to stop to avoid exception

        text_tokens = self.nlp(entry.lower())
        # self.extensive_logging and logging.info(f"We split the text in the lemmas: {text_tokens}")
        return [token.lemma_ for token in text_tokens
                if not token.is_punct and not token.is_currency and str(token) not in self.stop_words]

    def pre_process(self, entry: str) -> str | None:
        try:
            entry = self.detect_language(entry)
            entry = self._make_text_lemmas(entry)

            # We return None if the text is to be ignored
            return None if entry is None else "".join([f"{e} " for e in entry])
        except Exception as e:
            logging.error(e)
            # Something went wrong when processing the text, so we simply skip it
            logging.info(f"We had a problem processing the text {entry}")
            return None


def pre_process_corpus(resource_file_path: str = "./data/corpus.csv",
                       target_file_path: str = "./data/corpus.preprocessed.csv", overwrite: bool = False):
    """

    @param resource_file_path:
    @param target_file_path:
    @param overwrite:
    @return:
    """
    if os.path.exists(target_file_path) and not overwrite:
        logging.info("Procedure aborted as we are not allowed to override and the file already exists")
        return  # Abort the process.

    if not os.path.exists(resource_file_path):
        logging.info("Procedure aborted as the source file is missing")
        return  # Abort the process.

    reference_dataframe = pd.read_csv(resource_file_path)
    ps = PreProcessingService()
    save_frame = pd.DataFrame()

    save_frame["comments"] = reference_dataframe["comments"].swifter.apply(ps.pre_process)
    save_frame = save_frame.dropna()
    save_frame.to_csv(target_file_path, mode="w", header=True, index=False)
