# SPDX-FileCopyrightText: © 2024 Tenstorrent AI ULC

# SPDX-License-Identifier: Apache-2.0

import os
import pytest

import pytest
import torch
from torch import nn

import forge
from forge.op.eval.common import compare_with_golden_pcc, compare_with_golden


@pytest.mark.push
def test_multiple_inputs():
    class MultipleInputs(nn.Module):
        def __init__(self):
            super().__init__()

        def forward(self, a, b, c):
            return a + b + c

    inputs = [torch.rand(1, 32, 32), torch.rand(1, 32, 32), torch.rand(1, 32, 32)]

    framework_model = MultipleInputs()
    fw_out = framework_model(*inputs)

    compiled_model = forge.compile(framework_model, sample_inputs=inputs)
    co_out = compiled_model(*inputs)

    co_out = [co.to("cpu") for co in co_out]
    assert [compare_with_golden_pcc(golden=fo, calculated=co, pcc=0.99) for fo, co in zip(fw_out, co_out)]


@pytest.mark.parametrize(
    "a_shape, b_shape, c_shape",
    [
        ((1, 1, 32, 64), (1, 1, 64, 128), (1, 1, 128, 32)),
    ],
)
@pytest.mark.push
def test_input_order(a_shape, b_shape, c_shape):
    class InputOrderWithConstants(nn.Module):
        def __init__(self):
            super().__init__()
            self.const1 = torch.rand(1, 1, 32, 32)
            self.const2 = torch.rand(1, 1, 32, 32)

        def forward(self, a, b, c):
            x = torch.matmul(a, b)
            x = torch.matmul(x, c)
            x = x + self.const1
            x = x * self.const2
            return x

    a = torch.rand(*a_shape)
    b = torch.rand(*b_shape)
    c = torch.rand(*c_shape)

    framework_model = InputOrderWithConstants()
    fw_out = framework_model(a, b, c)

    compiled_model = forge.compile(framework_model, sample_inputs=[a, b, c])
    co_out = compiled_model(a, b, c)

    assert compare_with_golden_pcc(golden=fw_out, calculated=co_out[0][0], pcc=0.99)


@pytest.mark.parametrize("batch_size", [1, 4, 16, 32, 64])
@pytest.mark.parametrize("linear_features", [(784, 10)])
@pytest.mark.push
def test_matmul_bias(batch_size, linear_features):
    input_features, output_dim = linear_features

    class Linear(nn.Module):
        def __init__(self):
            super().__init__()
            self.l1 = nn.Linear(input_features, output_dim, bias=True)

        def forward(self, a):
            return self.l1(a)

    inputs = [torch.rand(batch_size, input_features)]

    framework_model = Linear()
    fw_out = framework_model(*inputs)

    compiled_model = forge.compile(framework_model, sample_inputs=inputs)
    co_out = compiled_model(*inputs)

    co_out = [co.to("cpu") for co in co_out]
    fw_out = [fw_out] if isinstance(fw_out, torch.Tensor) else fw_out
    assert all([compare_with_golden_pcc(golden=fo, calculated=co, pcc=0.99) for fo, co in zip(fw_out, co_out)])


@pytest.mark.parametrize("batch_size", [1, 2, 16, 64, 512])
@pytest.mark.parametrize("in_features", [784])
@pytest.mark.parametrize("out_features", [10])
@pytest.mark.push
def test_batch_size_inference(batch_size, in_features, out_features):
    class SimpleModel(nn.Module):
        def __init__(self):
            super(SimpleModel, self).__init__()
            self.linear = nn.Linear(in_features, out_features)

        def forward(self, x):
            y = self.linear(x)
            return nn.functional.softmax(y, dim=-1)

    in_data = torch.rand(batch_size, in_features)
    out_data = torch.randint(0, out_features, (batch_size,))

    model = SimpleModel()

    tt_model = forge.compile(model, sample_inputs=[torch.rand(batch_size, in_features)])

    pred = tt_model(in_data)[0]
    golden_pred = model(in_data)
    assert compare_with_golden(golden_pred, pred, pcc=0.95)  # 0.95 is the minimum value for which the test passes


@pytest.mark.parametrize("batch_size", [1, 2, 16, 64, 512])
@pytest.mark.parametrize("in_features", [784])
@pytest.mark.parametrize("out_features", [10])
@pytest.mark.push
def test_batch_size_training(batch_size, in_features, out_features):
    class SimpleModel(nn.Module):
        def __init__(self):
            super(SimpleModel, self).__init__()
            self.linear = nn.Linear(in_features, out_features)

        def forward(self, x):
            y = self.linear(x)
            return nn.functional.softmax(y, dim=-1)

    in_data = torch.rand(batch_size, in_features)
    out_data = torch.randint(0, out_features, (batch_size,))
    target = nn.functional.one_hot(out_data, num_classes=out_features).float()

    model = SimpleModel()

    loss_fn = torch.nn.CrossEntropyLoss()
    optimizer = torch.optim.SGD(model.parameters(), lr=0.001)
    tt_model = forge.compile(
        model, sample_inputs=[torch.rand(batch_size, in_features)], loss=loss_fn, optimizer=optimizer
    )

    optimizer.zero_grad()

    pred = tt_model(in_data)[0]
    golden_pred = model(in_data)
    assert compare_with_golden(golden_pred, pred, pcc=0.95)  # 0.95 is the minimum value for which the test passes

    loss = loss_fn(pred, target)
    golden_loss = loss_fn(golden_pred, target)
    assert torch.allclose(loss, golden_loss, rtol=1e-2)  # 1e-2 is the minimum value for which the test passes

    loss.backward()
    tt_model.backward()
