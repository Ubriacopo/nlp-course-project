import torch
from gensim import corpora
from gensim.models import CoherenceModel


class ABAEEvaluationProcessor:
    def __init__(self, aspects: torch.tensor, word_embeddings: torch.tensor, inverse_vocabulary: dict):
        # Normalize the data.
        self.aspects = torch.nn.functional.normalize(aspects, p=2, dim=-1)
        self.w_embeddings = torch.nn.functional.normalize(word_embeddings, p=2, dim=-1)

        self.top_k_words_per_aspect: dict = dict()

        self.inverse_vocabulary = inverse_vocabulary

    @staticmethod
    def from_model():  # Builder like
        pass

    def top_k_aspect_words(self, aspect_index: int, top_k: int, verbose: bool = True):

        if str(aspect_index) in self.top_k_words_per_aspect:
            return self.top_k_words_per_aspect[str(aspect_index)]

        if aspect_index < 0 or aspect_index >= len(self.aspects):
            raise "Index out of range"

        similarity = self.w_embeddings.matmul(self.aspects[aspect_index])
        ordered_words = similarity.argsort(descending=True)

        top_k_words = [(self.inverse_vocabulary[w], similarity[w], w) for w in ordered_words[:top_k]]

        if verbose:
            print(f"For aspect {aspect_index} most representative words are:\n")
            [print("Word: ", i[0], f"({i[1]})") for i in top_k_words]

        # To avoid re-computing
        self.top_k_words_per_aspect[str(aspect_index)] = top_k_words
        return top_k_words

    def aspect_coherence(self):
        pass


# todo check
def normalize(matrix: torch.tensor) -> torch.tensor:
    return matrix / torch.linalg.norm(matrix, dim=-1, keepdim=True)


def get_aspect_top_k_words(aspect: torch.tensor, word_embeddings,
                           inverse_vocabulary: dict, top_k: int = 25, verbose: bool = True) -> list:
    """
    This function will extract the top k words of each aspect.
    @return: A list of aspects with their top k words. [str, float, int]
    """
    similarity = word_embeddings @ aspect.T
    ordered_words = torch.argsort(similarity, descending=True)

    top_k_words = [(inverse_vocabulary[w], similarity[w], w) for w in ordered_words[:top_k]]

    if verbose:
        print("\nGiven aspect most representative words are:")
        [print("Word: ", i[0], f"({i[1]})") for i in top_k_words]

    return top_k_words


# todo: Prova con class gensim.models.coherencemodel.CoherenceModel
# https://stackoverflow.com/questions/66877729/calculate-coherence-for-non-gensim-topic-model
def coherence_per_aspect(aspects: list[list], text_dataset: list[str], topn=2) -> tuple[list, CoherenceModel]:
    """
    @param aspects: As most representative words (list of list of string).
    @param text_dataset: The dataset we measure coherence on.
    @param topn: On how many of the topn track the coherence value
    @return: Coherence per topic and the model itself as well.
    """
    if topn > len(aspects[0]):
        raise "I cannot take top n that is over the number of top words provided!"

    dictionary = corpora.Dictionary()

    # Gensim wants the corpus in BOW format:
    corpus = [dictionary.doc2bow(doc.split(" "), allow_update=True) for doc in text_dataset]
    coh_model = CoherenceModel(topics=aspects, corpus=corpus, dictionary=dictionary, coherence='u_mass', topn=topn)
    return coh_model.get_coherence_per_topic(), coh_model


def coherence_model_generation(aspects: list[list], ds, dictionary, topn=10):
    if topn > len(aspects[0]):
        raise "I cannot take top n that is over the number of top words provided!"
    coh_model = CoherenceModel(topics=aspects, dictionary=dictionary, texts=ds, coherence='c_npmi', topn=topn)
    return coh_model.get_coherence_per_topic(), coh_model

