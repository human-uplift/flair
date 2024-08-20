import math
from pathlib import Path
from typing import List, Optional, Tuple, Union

import torch
from torch import logsumexp, nn
from torch.nn import Transformer
from torch.optim import Optimizer
from transformers import PreTrainedTokenizerBase, AutoTokenizer

import flair
from flair.data import Dictionary
from flair.nn.recurrent import create_recurrent_layer


class LanguageModelTokenizer:

    def encode_as_sequence(self, texts: List[str]) -> torch.Tensor:
        pass

    def encode_as_batch(self, texts: List[str]) -> torch.Tensor:
        pass

    def decode(self, ids: List[int]) -> str:
        pass

    def vocab_size(self) -> int:
        pass


class CharacterTokenizer(LanguageModelTokenizer):

    def __init__(self, dictionary: Union[str, Dictionary] = "chars"):

        if isinstance(dictionary, str):
            self.dictionary: Dictionary = Dictionary.load(dictionary)
        else:
            self.dictionary: Dictionary = dictionary

    def encode_as_sequence(self, texts: List[str]) -> torch.Tensor:
        lines = [list(line) for line in texts]
        ids = torch.tensor(
            [self.dictionary.get_idx_for_item(char) for chars in lines for char in chars],
            dtype=torch.long,
        )
        return ids

    def vocab_size(self) -> int:
        return len(self.dictionary)

    def decode(self, ids: List[int]) -> str:

        words = [self.dictionary.get_item_for_index(id) for id in ids]
        return "".join(words)


class SubwordTokenizer(LanguageModelTokenizer):
    def __init__(self, subtokenizer: Union[str, PreTrainedTokenizerBase], padded_size=None):

        if isinstance(subtokenizer, str):
            self.subtokenizer: PreTrainedTokenizerBase = AutoTokenizer.from_pretrained(subtokenizer)
        else:
            self.subtokenizer = subtokenizer

        self.padded_size = padded_size

    def encode_as_batch(self, texts: List[str]) -> torch.Tensor:
        self.subtokenizer.pad_token = self.subtokenizer.eos_token
        ids = self.subtokenizer(texts, return_tensors="pt", padding=True).input_ids
        return ids

    def encode_as_sequence(self, texts: List[str]) -> torch.Tensor:
        lines = "".join(texts)
        ids = self.subtokenizer(lines, return_tensors="pt").input_ids.flatten()
        return ids

    def vocab_size(self) -> int:
        if self.padded_size:
            return self.padded_size
        return len(self.subtokenizer)

    def decode(self, ids: List[int]) -> str:
        words = self.subtokenizer.decode(ids)
        return words


class LanguageModel(nn.Module):
    """Container module with an encoder, a recurrent module, and a decoder."""

    def __init__(
        self,
        tokenizer: LanguageModelTokenizer,
        is_forward_lm: bool,
        hidden_size: int,
        nlayers: int,
        embedding_size: int = 100,
        attention_heads: int = 0,
        nout=None,
        document_delimiter: str = "\n",
        dropout=0.1,
        recurrent_type="LSTM",
        has_decoder=True,
        decoder_bias=True,
    ) -> None:
        super().__init__()

        self.tokenizer = tokenizer
        self.document_delimiter = document_delimiter
        self.is_forward_lm: bool = is_forward_lm

        self.dropout = dropout
        self.hidden_size = hidden_size
        self.embedding_size = embedding_size
        self.nlayers = nlayers

        self.drop = nn.Dropout(dropout)
        self.encoder = nn.Embedding(self.tokenizer.vocab_size(), embedding_size)

        self.rnn, self.state_count = create_recurrent_layer(
            recurrent_type, embedding_size, hidden_size, nlayers, dropout
        )
        self.recurrent_type = recurrent_type
        self.hidden = None

        self.nout = nout
        if nout is not None:
            self.proj: Optional[nn.Linear] = nn.Linear(hidden_size, nout)
            self.initialize(self.proj.weight)
            hidden_size = nout
        else:
            self.proj = None

        self.attention_heads = attention_heads
        if attention_heads:
            self.attention = torch.nn.MultiheadAttention(hidden_size, num_heads=attention_heads)

        if has_decoder:
            self.decoder: Optional[nn.Linear] = nn.Linear(hidden_size, self.tokenizer.vocab_size(), bias=decoder_bias)
        else:
            self.decoder = None

        self.init_weights()

        # auto-spawn on GPU if available
        self.to(flair.device)

    def init_weights(self):
        initrange = 0.1
        self.encoder.weight.detach().uniform_(-initrange, initrange)
        if self.decoder is not None:
            if self.decoder.bias is not None:
                self.decoder.bias.detach().fill_(0)
            self.decoder.weight.detach().uniform_(-initrange, initrange)

    def set_hidden(self, hidden):
        self.hidden = hidden

    def forward(self, input, hidden, ordered_sequence_lengths=None, decode=True):
        encoded = self.encoder(input)
        emb = self.drop(encoded)

        if hasattr(self.rnn, "flatten_parameters"):
            self.rnn.flatten_parameters()

        if len(hidden) == 1:
            output, h = self.rnn(emb, hidden[0])
            hidden = (h,)
        else:
            output, hidden = self.rnn(emb, hidden)

        if self.proj is not None:
            output = self.proj(output)

        output = self.drop(output)

        if self.attention_heads > 0:
            attended = self.attention.forward(
                query=output,
                key=output,
                value=output,
                attn_mask=Transformer.generate_square_subsequent_mask(output.size(0)),
                is_causal=True,
                need_weights=False,
            )[0]

            output = output + attended

        if decode:
            decoded = self.decoder(output)

            return (
                decoded,
                output,
                hidden,
            )
        else:
            return output, hidden

    def init_hidden(self, bsz):
        weight = next(self.parameters()).detach()
        return tuple(
            weight.new(self.nlayers, bsz, self.hidden_size).zero_().clone().detach() for _ in range(self.state_count)
        )

    def get_representation(
        self,
        strings: List[str],
        start_marker: str,
        end_marker: str,
        chars_per_chunk: int = 512,
    ):

        # pad strings with whitespaces to the longest sentence
        padded_strings: List[str] = []

        for string in strings:
            if not self.is_forward_lm:
                string = string[::-1]

            padded = f"{start_marker}{string}{end_marker}"
            padded_strings.append(padded)

        encoded = self.tokenizer.encode_as_batch(padded_strings).to(flair.device)

        hidden = self.init_hidden(encoded.size(0))

        batch = encoded.transpose(0, 1)
        rnn_output, hidden = self.forward(batch, hidden, decode=False)
        return rnn_output

    def get_output(self, text: str):
        char_indices = [self.dictionary.get_idx_for_item(char) for char in text]
        input_vector = torch.LongTensor([char_indices]).transpose(0, 1)

        hidden = self.init_hidden(1)
        prediction, rnn_output, hidden = self.forward(input_vector, hidden)

        return self.repackage_hidden(hidden)

    def repackage_hidden(self, h):
        """Wraps hidden states in new Variables, to detach them from their history."""
        if type(h) == torch.Tensor:
            return h.clone().detach()
        else:
            return tuple(self.repackage_hidden(v) for v in h)

    @staticmethod
    def initialize(matrix):
        in_, out_ = matrix.size()
        stdv = math.sqrt(3.0 / (in_ + out_))
        matrix.detach().uniform_(-stdv, stdv)

    @classmethod
    def load_language_model(cls, model_file: Union[Path, str], has_decoder=True):
        state = torch.load(str(model_file), map_location=flair.device)

        # print(state.keys())

        document_delimiter = state.get("document_delimiter", "\n")
        has_decoder = state.get("has_decoder", True) and has_decoder

        tokenizer = state.get("tokenizer", CharacterTokenizer(state.get("dictionary", "chars")))

        model = cls(
            tokenizer=tokenizer,
            is_forward_lm=state["is_forward_lm"],
            hidden_size=state["hidden_size"],
            nlayers=state["nlayers"],
            embedding_size=state["embedding_size"],
            nout=state["nout"],
            document_delimiter=document_delimiter,
            dropout=state["dropout"],
            recurrent_type=state.get("recurrent_type", "lstm"),
            attention_heads=0,
            has_decoder=has_decoder,
        )
        model.load_state_dict(state["state_dict"], strict=has_decoder)
        model.eval()
        model.to(flair.device)

        return model

    @classmethod
    def load_checkpoint(cls, model_file: Union[Path, str]):
        state = torch.load(str(model_file), map_location=flair.device)

        epoch = state.get("epoch")
        split = state.get("split")
        loss = state.get("loss")
        document_delimiter = state.get("document_delimiter", "\n")

        optimizer_state_dict = state.get("optimizer_state_dict")

        model = cls(
            tokenizer=state["tokenizer"],
            is_forward_lm=state["is_forward_lm"],
            hidden_size=state["hidden_size"],
            nlayers=state["nlayers"],
            embedding_size=state["embedding_size"],
            nout=state["nout"],
            document_delimiter=document_delimiter,
            dropout=state["dropout"],
            recurrent_type=state.get("recurrent_type", "lstm"),
        )
        model.load_state_dict(state["state_dict"])
        model.eval()
        model.to(flair.device)

        return {
            "model": model,
            "epoch": epoch,
            "split": split,
            "loss": loss,
            "optimizer_state_dict": optimizer_state_dict,
        }

    def save_checkpoint(
        self,
        file: Union[Path, str],
        optimizer: Optimizer,
        epoch: int,
        split: int,
        loss: float,
    ):
        model_state = {
            "state_dict": self.state_dict(),
            "tokenizer": self.tokenizer,
            "is_forward_lm": self.is_forward_lm,
            "hidden_size": self.hidden_size,
            "nlayers": self.nlayers,
            "embedding_size": self.embedding_size,
            "nout": self.nout,
            "document_delimiter": self.document_delimiter,
            "dropout": self.dropout,
            "optimizer_state_dict": optimizer.state_dict(),
            "epoch": epoch,
            "split": split,
            "loss": loss,
            "recurrent_type": self.recurrent_type,
            "has_decoder": self.decoder is not None,
        }

        torch.save(model_state, str(file), pickle_protocol=4)

    def save(self, file: Union[Path, str]):
        model_state = {
            "state_dict": self.state_dict(),
            "tokenizer": self.tokenizer,
            "is_forward_lm": self.is_forward_lm,
            "hidden_size": self.hidden_size,
            "nlayers": self.nlayers,
            "embedding_size": self.embedding_size,
            "nout": self.nout,
            "document_delimiter": self.document_delimiter,
            "dropout": self.dropout,
            "recurrent_type": self.recurrent_type,
            "has_decoder": self.decoder is not None,
        }

        torch.save(model_state, str(file), pickle_protocol=4)

    def generate_text(
        self,
        prefix: str = "",
        number_of_characters: int = 1000,
        temperature: float = 1.0,
        break_on_suffix=None,
        multi_label: bool = False,
    ) -> Tuple[str, float]:
        if prefix == "":
            prefix = self.document_delimiter

        with torch.no_grad():
            decoded_ids: List[int] = []

            # initial hidden state
            hidden = self.init_hidden(1)

            # print(prefix)
            input = self.tokenizer.encode_as_sequence([prefix]).to(flair.device).unsqueeze(0).transpose(0, 1)
            # print(input)

            if input.size(0) > 1:

                all_until_last = input[:-1]

                prediction, _, hidden = self.forward(all_until_last, hidden)

            input = input[-1].unsqueeze(0)
            log_prob = torch.zeros(1, device=flair.device)

            for _i in range(number_of_characters):
                input = input.to(flair.device)

                # get predicted weights
                prediction, _, hidden = self.forward(input, hidden)
                prediction = prediction.squeeze().detach()
                decoder_output = prediction

                if not multi_label:
                    # divide by temperature
                    prediction = prediction.div(temperature)

                    # to prevent overflow problem with small temperature values, substract largest value from all
                    # this makes a vector in which the largest value is 0
                    max = torch.max(prediction)
                    prediction -= max

                    # compute word weights with exponential function
                    word_weights = prediction.exp().cpu()

                else:
                    word_weights = torch.sigmoid(prediction)
                    # print(word_weights)

                # try sampling multinomial distribution for next character
                try:
                    word_idx = torch.multinomial(word_weights, 1)[0]
                except:  # noqa: E722 TODO: figure out exception type
                    word_idx = torch.tensor(0)

                # print(word_idx)
                prob = decoder_output[word_idx] - logsumexp(decoder_output, dim=0)
                log_prob += prob

                input = word_idx.detach().unsqueeze(0).unsqueeze(0)
                decoded_ids.append(input.item())

            text = prefix + self.tokenizer.decode(decoded_ids)

            log_prob_float = log_prob.item()
            log_prob_float /= len(decoded_ids)

            if not self.is_forward_lm:
                text = text[::-1]

            return text, -log_prob_float

    def calculate_perplexity(self, text: str) -> float:
        if not self.is_forward_lm:
            text = text[::-1]

        # input ids
        input = torch.tensor([self.dictionary.get_idx_for_item(char) for char in text[:-1]]).unsqueeze(1)
        input = input.to(flair.device)

        # push list of character IDs through model
        hidden = self.init_hidden(1)
        prediction, _, hidden = self.forward(input, hidden)

        # the target is always the next character
        targets = torch.tensor([self.dictionary.get_idx_for_item(char) for char in text[1:]])
        targets = targets.to(flair.device)

        # use cross entropy loss to compare output of forward pass with targets
        cross_entroy_loss = torch.nn.CrossEntropyLoss()
        loss = cross_entroy_loss(prediction.view(-1, len(self.dictionary)), targets).item()

        # exponentiate cross-entropy loss to calculate perplexity
        perplexity = math.exp(loss)

        return perplexity

    def __getstate__(self):
        # "document_delimiter" property may be missing in some older pre-trained models
        self.document_delimiter = getattr(self, "document_delimiter", "\n")

        # serialize the language models and the constructor arguments (but nothing else)
        model_state = {
            "state_dict": self.state_dict(),
            "dictionary": self.dictionary,
            "is_forward_lm": self.is_forward_lm,
            "hidden_size": self.hidden_size,
            "nlayers": self.nlayers,
            "embedding_size": self.embedding_size,
            "nout": self.nout,
            "document_delimiter": self.document_delimiter,
            "dropout": self.dropout,
            "recurrent_type": self.recurrent_type,
            "has_decoder": self.decoder is not None,
        }

        return model_state

    def __setstate__(self, d):
        # special handling for deserializing language models
        if "state_dict" in d:
            # re-initialize language model with constructor arguments
            language_model = LanguageModel(
                dictionary=d["dictionary"],
                is_forward_lm=d["is_forward_lm"],
                hidden_size=d["hidden_size"],
                nlayers=d["nlayers"],
                embedding_size=d["embedding_size"],
                nout=d["nout"],
                document_delimiter=d["document_delimiter"],
                dropout=d["dropout"],
                recurrent_type=d.get("recurrent_type", "lstm"),
                has_decoder=d.get("has_decoder", True),
            )

            language_model.load_state_dict(d["state_dict"], strict=d.get("has_decoder", True))

            # copy over state dictionary to self
            for key in language_model.__dict__:
                self.__dict__[key] = language_model.__dict__[key]

            # set the language model to eval() by default (this is necessary since FlairEmbeddings "protect" the LM
            # in their "self.train()" method)
            self.eval()

        else:
            if "recurrent_type" not in d:
                d["recurrent_type"] = "lstm"
            if "state_count" not in d:
                d["state_count"] = 2
            super().__setstate__(d)

    def _apply(self, fn):
        # models that were serialized using torch versions older than 1.4.0 lack the _flat_weights_names attribute
        # check if this is the case and if so, set it
        for child_module in self.children():
            if isinstance(child_module, torch.nn.RNNBase) and not hasattr(child_module, "_flat_weights_names"):
                _flat_weights_names = []

                num_direction = 2 if child_module.__dict__["bidirectional"] else 1
                for layer in range(child_module.__dict__["num_layers"]):
                    for direction in range(num_direction):
                        suffix = "_reverse" if direction == 1 else ""
                        param_names = ["weight_ih_l{}{}", "weight_hh_l{}{}"]
                        if child_module.__dict__["bias"]:
                            param_names += ["bias_ih_l{}{}", "bias_hh_l{}{}"]
                        param_names = [x.format(layer, suffix) for x in param_names]
                        _flat_weights_names.extend(param_names)

                child_module._flat_weights_names = _flat_weights_names

            child_module._apply(fn)
