from typing import Callable, Optional, Union
from tqdm import tqdm
import os
import torch
import torchaudio
import torchaudio.functional as F
from torch.utils.data import Dataset, DataLoader, IterableDataset, random_split
from pytorch_lightning import LightningDataModule
import webdataset


class VLSP2020Dataset(Dataset):
    def __init__(self, root: str, sample_rate: int = 16000):
        super().__init__()

        self.sample_rate = sample_rate
        self.memory = self._prepare_data(root)
        self._memory = tuple(
            (v["transcript"], v["audio"]) for v in self.memory.values()
        )

    @staticmethod
    def _prepare_data(root: str):
        memory = {}

        for f in os.scandir(root):
            file_name, file_ext = os.path.splitext(f.name)

            if file_ext == ".txt":
                if file_name not in memory:
                    memory[file_name] = {"transcript": f.path}
                elif "transcript" not in memory[file_name]:
                    memory[file_name]["transcript"] = f.path
                else:
                    raise ValueError(f"Duplicate transcript for {f.path}")
            else:
                if file_name not in memory:
                    memory[file_name] = {"audio": f.path}
                elif "audio" not in memory[file_name]:
                    memory[file_name]["audio"] = f.path
                else:
                    raise ValueError(f"Duplicate audio for {f.path}")

        for key, value in memory.items():
            if "audio" not in value:
                raise ValueError(f"Missing audio for {key}")
            elif "transcript" not in value:
                raise ValueError(f"Missing transcript for {key}")

        return memory

    def __len__(self):
        return len(self.memory)

    def __getitem__(self, index: int):
        transcript, audio = self._memory[index]

        with open(transcript, "r") as f:
            transcript = f.read()

        audio, sample_rate = torchaudio.load(audio)
        audio = F.resample(audio, sample_rate, self.sample_rate)

        return transcript, audio


class VLSP2020TarDataset:
    def __init__(self, outpath: str):
        self.outpath = outpath

    def convert(self, dataset: VLSP2020Dataset):
        writer = webdataset.TarWriter(self.outpath)

        for idx, (transcript, audio) in enumerate(tqdm(dataset, colour="green")):
            writer.write(
                {
                    "__key__": f"{idx:08d}",
                    "txt": transcript,
                    "pth": audio,
                }
            )

        writer.close()

    def load(self) -> webdataset.WebDataset:
        self.data = (
            webdataset.WebDataset(self.outpath)
            .decode(
                webdataset.handle_extension("txt", lambda x: x.decode("utf-8")),
                webdataset.torch_audio,
            )
            .to_tuple("txt", "pth")
        )

        return self.data


def get_dataloader(
    dataset: Union[VLSP2020Dataset, webdataset.WebDataset],
    return_transcript: bool = False,
    target_transform: Optional[Callable] = None,
    batch_size: int = 32,
    num_workers: int = 2,
):
    def collate_fn(batch):
        def get_audio(item):
            audio = item[1]

            assert (
                isinstance(audio, torch.Tensor)
                and audio.ndim == 2
                and audio.size(0) == 1
            )

            return audio.squeeze(0)

        audio = tuple(get_audio(item) for item in batch)

        if return_transcript:
            if target_transform is not None:
                transcript = tuple(target_transform(item[0]) for item in batch)
            else:
                transcript = tuple(item[0] for item in batch)

            return transcript, audio
        else:
            return audio

    return DataLoader(
        dataset, batch_size=batch_size, num_workers=num_workers, collate_fn=collate_fn
    )
