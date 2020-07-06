import pytest
import torch
from numpy.testing import assert_allclose

from onnxruntime.capi.training import orttrainer_options as orttrainer_options
from onnxruntime.capi.training import orttrainer
from onnxruntime.capi.training import optim, TrainStepInfo
from onnxruntime.capi.training import amp
from onnxruntime.capi.training import model_desc_validation as md_val


@pytest.mark.parametrize("test_input", [
    ({}),
    ({'batch': {},
      'device': {},
      'distributed': {},
      'mixed_precision': {},
      'utils': {},
      '_internal_use': {}})
])
def testORTTrainerOptionsDefaultValues(test_input):
    ''' Test different ways of using default values for incomplete input'''

    expected_values = {
        'batch': {
            'gradient_accumulation_steps': 0
        },
        'device': {
            'id': None,
            'mem_limit': 0
        },
        'distributed': {
            'world_rank': 0,
            'world_size': 1,
            'local_rank': 0,
            'allreduce_post_accumulation': False,
            'enable_partition_optimizer': False,
            'enable_adasum': False
        },
        'lr_scheduler': None,
        'mixed_precision': {
            'enabled': False,
            'loss_scaler': None
        },
        'utils': {
            'grad_norm_clip': False
        },
        '_internal_use': {
            'frozen_weights': [],
            'enable_internal_postprocess': True,
            'extra_postprocess': None
        }
    }

    actual_values = orttrainer_options.ORTTrainerOptions(test_input)
    assert actual_values._validated_opts == expected_values


def testORTTrainerOptionsInvalidMixedPrecisionEnabledSchema():
    '''Test an invalid input based on schema validation error message'''

    expected_msg = "Invalid options: {'mixed_precision': [{'enabled': ['must be of boolean type']}]}"
    with pytest.raises(ValueError) as e:
        orttrainer_options.ORTTrainerOptions({'mixed_precision': {'enabled': 1}})
    assert str(e.value) == expected_msg


def testTrainStepInfo():
    '''Test valid initializations of TrainStepInfo'''

    step_info = orttrainer.TrainStepInfo(all_finite=True, epoch=1, step=2)
    assert step_info.all_finite is True
    assert step_info.epoch == 1
    assert step_info.step == 2

    step_info = orttrainer.TrainStepInfo()
    assert step_info.all_finite is None
    assert step_info.epoch is None
    assert step_info.step is None


@pytest.mark.parametrize("test_input", [
    (-1),
    ('Hello'),
])
def testTrainStepInfoInvalidAllFinite(test_input):
    '''Test invalid initialization of TrainStepInfo'''
    with pytest.raises(AssertionError):
        orttrainer.TrainStepInfo(all_finite=test_input)

    with pytest.raises(AssertionError):
        orttrainer.TrainStepInfo(epoch=test_input)

    with pytest.raises(AssertionError):
        orttrainer.TrainStepInfo(step=test_input)


@pytest.mark.parametrize("optim_name,lr,alpha,default_alpha", [
    ('AdamOptimizer', .1, .2, None),
    ('LambOptimizer', .2, .3, None),
    ('SGDOptimizer', .3, .4, None),
    ('SGDOptimizer', .3, .4, .5)
])
def testOptimizerConfig(optim_name, lr, alpha, default_alpha):
    '''Test initialization of _OptimizerConfig'''
    hyper_parameters = {'lr': lr, 'alpha': alpha}
    param_groups = [{'params': ['fc1.weight', 'fc2.weight']}]
    if default_alpha is not None:
        param_groups[0].update({'alpha': default_alpha})
    else:
        param_groups[0].update({'alpha': alpha})
    cfg = optim.config._OptimizerConfig(
        name=optim_name, hyper_parameters=hyper_parameters, param_groups=param_groups)

    assert cfg.name == optim_name
    rtol = 1e-03
    assert_allclose(hyper_parameters['lr'],
                    cfg.lr, rtol=rtol, err_msg="lr mismatch")

    # 1:1 mapping between hyper_parameters and param_groups's hyper parameters
    for group in param_groups:
        for k, _ in group.items():
            if k != 'params':
                assert k in cfg.hyper_parameters, "hyper parameter {k} not present in one of the parameter groups"
    for k, _ in cfg.hyper_parameters.items():
        for group in cfg.param_groups:
            assert k in group, "hyper parameter {k} not present in one of the parameter groups"


@pytest.mark.parametrize("optim_name,hyper_parameters,param_groups", [
    ('AdamOptimizer', {'lr': -1}, []),  # invalid lr
    ('FooOptimizer', {'lr': 0.001}, []),  # invalid name
    ('SGDOptimizer', [], []),  # invalid type(hyper_parameters)
    (optim.AdamConfig, {'lr': 0.003}, []),  # invalid type(name)
    ('AdamOptimizer', {'lr': None}, []),  # missing 'lr' hyper parameter
    ('SGDOptimizer', {'lr': 0.004}, {}),  # invalid type(param_groups)
    # invalid type(param_groups[i])
    ('AdamOptimizer', {'lr': 0.005, 'alpha': 2}, [[]]),
    # missing 'params' at 'param_groups'
    ('AdamOptimizer', {'lr': 0.005, 'alpha': 2}, [{'alpha': 1}]),
    # missing 'alpha' at 'hyper_parameters'
    ('AdamOptimizer', {'lr': 0.005}, [{'params': 'param1', 'alpha': 1}]),
])
def testOptimizerConfigInvalidInputs(optim_name, hyper_parameters, param_groups):
    '''Test invalid initialization of _OptimizerConfig'''

    with pytest.raises(AssertionError):
        optim.config._OptimizerConfig(
            name=optim_name, hyper_parameters=hyper_parameters, param_groups=param_groups)


def testSGDConfig():
    '''Test initialization of SGD'''
    cfg = optim.SGDConfig()
    assert cfg.name == 'SGDOptimizer'

    rtol = 1e-05
    assert_allclose(0.001, cfg.lr, rtol=rtol, err_msg="lr mismatch")

    cfg = optim.SGDConfig(lr=0.002)
    assert_allclose(0.002, cfg.lr, rtol=rtol, err_msg="lr mismatch")

    # SGD does not support param_groups
    with pytest.raises(AssertionError) as e:
        param_groups = [{'params': ['layer1.weight'], 'lr': 0.1}]
        optim.SGDConfig(param_groups=param_groups, lr=0.002)
        assert_allclose(0.002, cfg.lr, rtol=rtol, err_msg="lr mismatch")
    assert str(e.value) == "'param_groups' must be an empty list for SGD optimizer"


def testAdamConfig():
    '''Test initialization of Adam'''
    cfg = optim.AdamConfig()
    assert cfg.name == 'AdamOptimizer'

    rtol = 1e-05
    assert_allclose(0.001, cfg.lr, rtol=rtol, err_msg="lr mismatch")
    assert_allclose(0.9, cfg.alpha, rtol=rtol, err_msg="alpha mismatch")
    assert_allclose(0.999, cfg.beta, rtol=rtol, err_msg="beta mismatch")
    assert_allclose(0.0, cfg.lambda_coef, rtol=rtol,
                    err_msg="lambda_coef mismatch")
    assert_allclose(1e-8, cfg.epsilon, rtol=rtol, err_msg="epsilon mismatch")
    assert cfg.do_bias_correction == True, "lambda_coef mismatch"
    assert cfg.weight_decay_mode == True, "weight_decay_mode mismatch"


def testLambConfig():
    '''Test initialization of Lamb'''
    cfg = optim.LambConfig()
    assert cfg.name == 'LambOptimizer'
    rtol = 1e-05
    assert_allclose(0.001, cfg.lr, rtol=rtol, err_msg="lr mismatch")
    assert_allclose(0.9, cfg.alpha, rtol=rtol, err_msg="alpha mismatch")
    assert_allclose(0.999, cfg.beta, rtol=rtol, err_msg="beta mismatch")
    assert_allclose(0.0, cfg.lambda_coef, rtol=rtol,
                    err_msg="lambda_coef mismatch")
    assert cfg.ratio_min == float('-inf'), "ratio_min mismatch"
    assert cfg.ratio_max == float('inf'), "ratio_max mismatch"
    assert_allclose(1e-6, cfg.epsilon, rtol=rtol, err_msg="epsilon mismatch")
    assert cfg.do_bias_correction == True, "lambda_coef mismatch"


@pytest.mark.parametrize("optim_name", [
    ('Adam'),
    ('Lamb')
])
def testParamGroups(optim_name):
    rtol = 1e-5
    param_groups = [{'params': ['layer1.weight'], 'alpha': 0.1}]
    if optim_name == 'Adam':
        cfg = optim.AdamConfig(param_groups=param_groups, alpha=0.2)
    elif optim_name == 'Lamb':
        cfg = optim.LambConfig(param_groups=param_groups, alpha=0.2)
    else:
        raise ValueError('invalid input')
    assert len(cfg.param_groups) == 1, "param_groups should have length 1"
    assert_allclose(cfg.param_groups[0]['alpha'], 0.1,
                    rtol=rtol, err_msg="invalid lr on param_groups[0]")


@pytest.mark.parametrize("optim_name", [
    ('Adam'),
    ('Lamb')
])
def testInvalidParamGroups(optim_name):
    # lr is not supported within param_groups
    with pytest.raises(AssertionError) as e:
        param_groups = [{'params': ['layer1.weight'], 'lr': 0.1}]
        if optim_name == 'Adam':
            optim.AdamConfig(param_groups=param_groups, lr=0.2)
        elif optim_name == 'Lamb':
            optim.LambConfig(param_groups=param_groups, lr=0.2)
        else:
            raise ValueError('invalid input')
    assert str(e.value) == "'lr' is not supported inside param_groups"


@pytest.mark.parametrize("optim_name", [
    ('AdamOptimizer'),
    ('LambOptimizer'),
    ('SGDOptimizer')
])
def testLinearLRSchedulerCreation(optim_name):
    rtol = 1e-03
    initial_lr = 0.5
    total_steps = 10
    warmup = 0.05

    if optim_name == 'AdamOptimizer':
        optimizer_config = optim.AdamConfig(lr=initial_lr)
    elif optim_name == 'LambOptimizer':
        optimizer_config = optim.LambConfig(lr=initial_lr)
    elif optim_name == 'SGDOptimizer':
        optimizer_config = optim.SGDConfig(lr=initial_lr)

    lr_scheduler = optim.lr_scheduler.LinearWarmupLRScheduler(optimizer_config,
                                                              total_steps,
                                                              warmup)

    # Initial state
    assert lr_scheduler.optimizer_config == optimizer_config
    assert lr_scheduler.total_steps == total_steps
    assert lr_scheduler.warmup == warmup
    assert_allclose(lr_scheduler.optimizer_config.hyper_parameters['lr'],
                    initial_lr, rtol=rtol, err_msg="lr mismatch")


@pytest.mark.parametrize("lr_scheduler,expected_values", [
    (optim.lr_scheduler.ConstantWarmupLRScheduler, [0.181818, 0.066116, 0.036063, 0.026228, 0.023843,
                                                    0.023843, 0.023843, 0.023843, 0.023843, 0.023843]),
    (optim.lr_scheduler.CosineWarmupLRScheduler, [0.181818, 0.066116, 0.036063, 0.026228, 0.023843,
                                                  0.010225, 0.002989, 0.0005158, 0.000040937, 0.0000008291]),
    (optim.lr_scheduler.LinearWarmupLRScheduler, [0.181818, 0.066116, 0.036063, 0.026228, 0.023843,
                                                  0.021675, 0.0157636, 0.0085983, 0.0031266, 0.00056847]),
    (optim.lr_scheduler.PolyWarmupLRScheduler, [0.181818, 0.066116, 0.036063, 0.026228, 0.023843,
                                                0.0160749, 0.0096935, 0.0050622, 0.0021585, 0.000650833])
])
def testLRSchedulerUpdateImpl(lr_scheduler, expected_values):
    rtol = 1e-04

    # Initial state
    initial_lr = 1
    total_steps = 10
    warmup = 0.5
    optimizer_config = optim.SGDConfig(lr=initial_lr)
    lr_scheduler = lr_scheduler(optimizer_config,
                                total_steps,
                                warmup)

    # First half is warmup
    for step in range(total_steps):
        # Emulate train step call
        train_step_info = TrainStepInfo(step=step)

        lr_scheduler._step(train_step_info)
        lr_list = lr_scheduler.get_last_lr()
        assert len(lr_list) == 1
        assert_allclose(lr_list[0],
                        expected_values[step], rtol=rtol, err_msg="lr mismatch")


def testDynamicLossScaler():
    rtol = 1e-5
    default_scaler = amp.loss_scaler.DynamicLossScaler()

    # Initial state
    train_step_info = orttrainer.TrainStepInfo(
        all_finite=True, epoch=0, step=0)
    assert_allclose(default_scaler.loss_scale, float(1 << 16),
                    rtol=rtol, err_msg="loss scale mismatch")
    assert default_scaler.up_scale_window == 2000
    assert_allclose(default_scaler.min_loss_scale, 1.0,
                    rtol=rtol, err_msg="min loss scale mismatch")
    assert_allclose(default_scaler.max_loss_scale, float(
        1 << 24), rtol=rtol, err_msg="max loss scale mismatch")

    # Performing 9*2000 updates to cover all branches of LossScaler.update(train_step_info.all_finite=True)
    loss_scale = float(1 << 16)
    for cycles in range(1, 10):

        # 1999 updates without overflow produces 1999 stable steps
        for i in range(1, 2000):
            default_scaler.update(train_step_info)
            assert default_scaler._stable_steps_count == i
            assert_allclose(default_scaler.loss_scale, loss_scale,
                            rtol=rtol, err_msg=f"loss scale mismatch at update {i}")

        # 2000th update without overflow doubles the loss and zero stable steps until max_loss_scale is reached
        default_scaler.update(train_step_info)
        if cycles <= 8:
            loss_scale *= 2
        assert default_scaler._stable_steps_count == 0
        assert_allclose(default_scaler.loss_scale, loss_scale,
                        rtol=rtol, err_msg="loss scale mismatch")

    # After 8 cycles, loss scale should be float(1 << 16)*(2**8)
    assert_allclose(default_scaler.loss_scale, float(1 << 16)
                    * (2**8), rtol=rtol, err_msg="loss scale mismatch")

    # After 9 cycles, loss scale reaches max_loss_scale and it is not doubled from that point on
    loss_scale = float(1 << 16)*(2**8)
    for count in range(1, 2050):
        default_scaler.update(train_step_info)
        assert default_scaler._stable_steps_count == (count % 2000)
        assert_allclose(default_scaler.loss_scale, loss_scale,
                        rtol=rtol, err_msg="loss scale mismatch")

    # Setting train_step_info.all_finite = False to test down scaling
    train_step_info.all_finite = False

    # Performing 24 updates to half the loss scale each time
    loss_scale = float(1 << 16)*(2**8)
    for count in range(1, 25):
        default_scaler.update(train_step_info)
        loss_scale /= 2
        assert default_scaler._stable_steps_count == 0
        assert_allclose(default_scaler.loss_scale, loss_scale,
                        rtol=rtol, err_msg="loss scale mismatch")

    # After 24 updates with gradient overflow, loss scale is 1.0
    assert_allclose(default_scaler.loss_scale, 1.,
                    rtol=rtol, err_msg="loss scale mismatch")

    # After 25 updates, min_loss_scale is reached and loss scale is not halfed from that point on
    for count in range(1, 5):
        default_scaler.update(train_step_info)
        assert default_scaler._stable_steps_count == 0
        assert_allclose(default_scaler.loss_scale, loss_scale,
                        rtol=rtol, err_msg="loss scale mismatch")


def testDynamicLossScalerCustomValues():
    rtol = 1e-5
    scaler = amp.loss_scaler.DynamicLossScaler(automatic_update=False,
                                               loss_scale=3,
                                               up_scale_window=7,
                                               min_loss_scale=5,
                                               max_loss_scale=10)
    assert scaler.automatic_update == False
    assert_allclose(scaler.loss_scale, 3, rtol=rtol,
                    err_msg="loss scale mismatch")
    assert_allclose(scaler.min_loss_scale, 5, rtol=rtol,
                    err_msg="min loss scale mismatch")
    assert_allclose(scaler.max_loss_scale, 10, rtol=rtol,
                    err_msg="max loss scale mismatch")
    assert scaler.up_scale_window == 7


@pytest.mark.parametrize("test_input", [
    ({'inputs': [('in0', [])],
      'outputs': [('out0', []), ('out1', [])]}),
    ({'inputs': [('in0', ['batch', 2, 3])],
      'outputs': [('out0', [], True)]}),
    ({'inputs': [('in0', []), ('in1', [1]), ('in2', [1, 2]), ('in3', [1000, 'dyn_ax1']), ('in4', ['dyn_ax1', 'dyn_ax2', 'dyn_ax3'])],
      'outputs': [('out0', [], True), ('out1', [1], False), ('out2', [1, 'dyn_ax1', 3])]})
])
def testORTTrainerModelDescValidSchemas(test_input):
    r''' Test different ways of using default values for incomplete input'''
    md_val._ORTTrainerModelDesc(test_input)


@pytest.mark.parametrize("test_input,error_msg", [
    ({'inputs': [(True, [])],
      'outputs': [(True, [])]},
     "Invalid model_desc: {'inputs': [{0: ['the first element of the tuple (aka name) must be a string']}], 'outputs': [{0: ['the first element of the tuple (aka name) must be a string']}]}"),
    ({'inputs': [('in1', None)],
      'outputs': [('out1', None)]},
     "Invalid model_desc: {'inputs': [{0: ['the second element of the tuple (aka shape) must be a list']}], 'outputs': [{0: ['the second element of the tuple (aka shape) must be a list']}]}"),
    ({'inputs': [('in1', [])],
      'outputs': [('out1', [], None)]},
     "Invalid model_desc: {'outputs': [{0: ['the third element of the tuple (aka is_loss) must be a boolean']}]}"),
    ({'inputs': [('in1', [True])],
      'outputs': [('out1', [True])]},
     "Invalid model_desc: {'inputs': [{0: ['each shape must be either a string or integer']}], 'outputs': [{0: ['each shape must be either a string or integer']}]}"),
    ({'inputs': [('in1', [])],
      'outputs': [('out1', [], True), ('out2', [], True)]},
     "Invalid model_desc: {'outputs': [{1: ['only one is_loss can bet set to True']}]}"),
])
def testORTTrainerModelDescInvalidSchemas(test_input, error_msg):
    r''' Test different ways of using default values for incomplete input'''
    with pytest.raises(ValueError) as e:
        md_val._ORTTrainerModelDesc(test_input)
    assert str(e.value) == error_msg

