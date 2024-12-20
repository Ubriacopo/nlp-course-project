from abc import abstractmethod
from dataclasses import dataclass
from pathlib import Path

from torch.utils.data import DataLoader

from core.utils import max_margin_loss
from core.embeddings import WordEmbedding, AspectEmbedding
from core.model import ABAEGenerator
from core.utils import LoadCorpusUtility


# todo fix file
@dataclass
class AbaeModelConfiguration:
    """
      Configuration for the ABAE model.

      Attributes:
          corpus_file: Path to the corpus file.
          model_name: str Name of model
          max_vocab_size: Maximum size of the vocabulary.
          embedding_size: Size of the word embeddings.
          aspect_embedding_size: Size of the aspect embeddings.
          aspect_size: Number of aspects.
          max_sequence_length: Maximum length of the input sequences.
          negative_sample_size: Number of negative samples.
          output_path: Where files are stored
    """
    corpus_file: str

    model_name: str = "abae_model"

    aspect_size: int = 16
    max_vocab_size: int = None
    embedding_size: int = 128
    aspect_embedding_size: int = 128

    max_sequence_length: int = 256
    negative_sample_size: int = 15

    output_path: str = "./output"


class AbaeModelManager:
    def __init__(self, config: AbaeModelConfiguration, override_existing: bool = False):
        super(AbaeModelManager, self).__init__()
        self.config = config
        Path(self.config.output_path).mkdir(parents=True, exist_ok=True)
        # Load the Embeddings
        self.embedding_model: WordEmbedding | None = None
        self.aspect_model: AspectEmbedding | None = None

        self.__load_embeddings(override_existing)
        self.model_generator: ABAEGenerator = ABAEGenerator(
            self.config.max_sequence_length, self.config.negative_sample_size, self.embedding_model, self.aspect_model
        )

        # The training model instance.
        self._t_model = None
        self._ev_model = None

    def __load_embeddings(self, override_existing: bool):
        c = self.config  # To make it more readable.
        keep = not override_existing

        load_utility = LoadCorpusUtility(column_name="comments")

        self.embedding_model = WordEmbedding(c.embedding_size, c.output_path, c.model_name, c.max_vocab_size)
        self.embedding_model.generate(corpus=load_utility.load_data(c.corpus_file), sg=True, load_existing=keep)

        self.aspect_model = AspectEmbedding(c.aspect_size, c.aspect_embedding_size, c.output_path, c.model_name)
        self.aspect_model.generate(embedding_weights=self.embedding_model.weights(), load_existing=keep)

    def prepare_evaluation_model(self):
        model_file_path = f"{self.config.output_path}/{self.config.model_name}.keras"
        self._ev_model = self.model_generator.make_model(model_file_path)
        return self._ev_model

    def prepare_training_model(self, consider_stored: bool = False, optimizer: str = 'SGD'):
        considered_path = f"{self.config.output_path}/{self.config.model_name}.keras" if consider_stored else None
        self._t_model = self.model_generator.make_training_model(considered_path)
        self._t_model.compile(optimizer=optimizer, loss=[max_margin_loss], metrics={'max_margin': max_margin_loss})
        return self._t_model

    def persist_model(self):
        if self._t_model is not None:
            self._t_model.save(f"{self.config.output_path}/{self.config.model_name}.keras")

    def prepare_model(self, train: bool = False):
        return self.prepare_training_model() if train else self.prepare_evaluation_model()
