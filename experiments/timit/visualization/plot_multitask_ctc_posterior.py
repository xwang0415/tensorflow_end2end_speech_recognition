#! /usr/bin/env python
# -*- coding: utf-8 -*-

"""Plot the trained Multi-task CTC posteriors (TIMIT corpus)."""

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import os
import sys
import tensorflow as tf
import yaml

sys.path.append('../')
sys.path.append('../../')
sys.path.append('../../../')
from data.read_dataset_multitask_ctc import DataSet
from models.ctc.load_model_multitask import load
from util_plot_ctc import posterior_test_multitask


def do_plot(network, label_type_second, num_stack, num_skip, epoch=None):
    """Plot the Multi-task CTC posteriors.
    Args:
        network: model to restore
        label_type_second: string, phone39 or phone48 or phone61
        num_stack: int, the number of frames to stack
        num_skip: int, the number of frames to skip
        epoch: int, the epoch to restore
    """
    # Load dataset
    test_data = DataSet(data_type='test', label_type_second='phone61',
                        batch_size=1,
                        num_stack=num_stack, num_skip=num_skip,
                        is_sorted=False, is_progressbar=True)

    # Define placeholders
    network.inputs = tf.placeholder(
        tf.float32,
        shape=[None, None, network.input_size],
        name='input')
    indices_pl = tf.placeholder(tf.int64, name='indices')
    values_pl = tf.placeholder(tf.int32, name='values')
    shape_pl = tf.placeholder(tf.int64, name='shape')
    network.labels = tf.SparseTensor(indices_pl, values_pl, shape_pl)
    indices_second_pl = tf.placeholder(tf.int64, name='indices_second')
    values_second_pl = tf.placeholder(tf.int32, name='values_second')
    shape_second_pl = tf.placeholder(tf.int64, name='shape_second')
    network.labels_second = tf.SparseTensor(indices_second_pl,
                                            values_second_pl,
                                            shape_second_pl)
    network.inputs_seq_len = tf.placeholder(tf.int64,
                                            shape=[None],
                                            name='inputs_seq_len')

    # Add to the graph each operation (including model definition)
    _, logits_main, logits_second = network.compute_loss(
        network.inputs,
        network.labels,
        network.labels_second,
        network.inputs_seq_len)
    posteriors_op_main, posteriors_op_second = network.posteriors(
        logits_main, logits_second)

    # Create a saver for writing training checkpoints
    saver = tf.train.Saver()

    with tf.Session() as sess:
        ckpt = tf.train.get_checkpoint_state(network.model_dir)

        # If check point exists
        if ckpt:
            # Use last saved model
            model_path = ckpt.model_checkpoint_path
            if epoch is not None:
                model_path = model_path.split('/')[:-1]
                model_path = '/'.join(model_path) + '/model.ckpt-' + str(epoch)
            saver.restore(sess, model_path)
            print("Model restored: " + model_path)
        else:
            raise ValueError('There are not any checkpoints.')

        # Visualize
        posterior_test_multitask(session=sess,
                                 posteriors_op_main=posteriors_op_main,
                                 posteriors_op_second=posteriors_op_second,
                                 network=network,
                                 dataset=test_data,
                                 label_type_second=label_type_second,
                                 save_path=network.model_dir)


def main(model_path):

    epoch = None  # if None, restore the final epoch

    # Load config file
    with open(os.path.join(model_path, 'config.yml'), "r") as f:
        config = yaml.load(f)
        corpus = config['corpus']
        feature = config['feature']
        param = config['param']

    if corpus['label_type_second'] == 'phone61':
        output_size_second = 61
    elif corpus['label_type_second'] == 'phone48':
        output_size_second = 48
    elif corpus['label_type_second'] == 'phone39':
        output_size_second = 39

    # Model setting
    CTCModel = load(model_type=config['model_name'])
    network = CTCModel(
        batch_size=1,
        input_size=feature['input_size'] * feature['num_stack'],
        num_unit=param['num_unit'],
        num_layer_main=param['num_layer_main'],
        num_layer_second=param['num_layer_second'],
        output_size_main=30,
        output_size_second=output_size_second,
        main_task_weight=param['main_task_weight'],
        clip_grad=param['clip_grad'],
        clip_activation=param['clip_activation'],
        dropout_ratio_input=param['dropout_input'],
        dropout_ratio_hidden=param['dropout_hidden'],
        num_proj=param['num_proj'],
        weight_decay=param['weight_decay'])

    network.model_dir = model_path
    print(network.model_dir)
    do_plot(network=network,
            label_type_second=corpus['label_type_second'],
            num_stack=feature['num_stack'],
            num_skip=feature['num_skip'],
            epoch=epoch)


if __name__ == '__main__':

    args = sys.argv
    if len(args) != 2:
        raise ValueError(
            ("Set a path to saved model.\n"
             "Usase: python restore_ctc.py path_to_saved_model"))
    main(model_path=args[1])
