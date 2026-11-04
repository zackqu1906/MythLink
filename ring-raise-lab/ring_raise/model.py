from __future__ import annotations

import torch
from torch import nn


class CausalConv(nn.Module):
    def __init__(self, cin, cout, kernel, stride):
        super().__init__()
        self.pad = nn.ConstantPad1d((kernel-1, 0), 0)
        self.conv = nn.Conv1d(cin, cout, kernel, stride=stride)

    def forward(self, x):
        return torch.relu(self.conv(self.pad(x)))


class RaiseCNN(nn.Module):
    def __init__(self):
        super().__init__()
        self.net = nn.Sequential(CausalConv(6, 16, 9, 2), CausalConv(16, 32, 5, 2),
                                 CausalConv(32, 32, 5, 2), nn.AdaptiveAvgPool1d(8),
                                 nn.Flatten(), nn.Linear(256, 4))

    def forward(self, x):
        return self.net(x)


class StatsLinear(nn.Module):
    """Small non-temporal baseline; same history and train-only scaling as CNN."""
    def __init__(self):
        super().__init__()
        self.head = nn.Linear(36, 4)

    def forward(self, x):
        q = max(1, x.shape[-1] // 4)
        features = torch.cat([x.mean(-1), x.std(-1, unbiased=False), x.amin(-1), x.amax(-1),
                              x[:, :, -1], x[:, :, -q:].mean(-1)-x[:, :, :q].mean(-1)], dim=1)
        return self.head(features)


def make_model(kind):
    if kind == 'cnn':
        return RaiseCNN()
    if kind == 'stats':
        return StatsLinear()
    raise ValueError('model 必须为 cnn 或 stats')
