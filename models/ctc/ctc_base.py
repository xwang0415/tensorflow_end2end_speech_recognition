#! /usr/bin/env python
# -*- coding: utf-8 -*-

"""Base class of CTC model."""

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import tensorflow as tf


OPTIMIZER_CLS_NAMES = {
    "adagrad": tf.train.AdagradOptimizer,
    "adadelta": tf.train.AdadeltaOptimizer,
    "adam": tf.train.AdamOptimizer,
    "momentum": tf.train.MomentumOptimizer,
    "rmsprop": tf.train.RMSPropOptimizer,
    "sgd": tf.train.GradientDescentOptimizer,
}


class ctcBase(object):
    """Connectionist Temporal Classification (CTC) network.
    Args:
        batch_size: int, batch size of mini batch
        input_size: int, the dimensions of input vectors
        num_unit: int, the number of units in each layer
        num_layer: int, the number of layers
        output_size: int, the number of nodes in softmax layer
            (except for blank class)
        parameter_init: A float value. Range of uniform distribution to
            initialize weight parameters
        clip_grad: A float value. Range of gradient clipping (> 0)
        clip_activation: A float value. Range of activation clipping (> 0)
        dropout_ratio_input: A float value. Dropout ratio in input-hidden
            layers
        dropout_ratio_hidden: A float value. Dropout ratio in hidden-hidden
            layers
        weight_decay: A float value. Regularization parameter for weight decay
    """

    def __init__(self,
                 batch_size,
                 input_size,
                 num_unit,
                 num_layer,
                 output_size,
                 parameter_init,
                 clip_grad,
                 clip_activation,
                 dropout_ratio_input,
                 dropout_ratio_hidden,
                 weight_decay,
                 name=None):

        # Network size
        self.batch_size = batch_size
        self.input_size = input_size
        self.output_size = output_size
        self.num_unit = num_unit
        self.num_layer = num_layer
        self.num_classes = output_size + 1  # plus blank label

        # Regularization
        self.parameter_init = parameter_init
        self.clip_grad = clip_grad
        self.clip_activation = clip_activation
        self.dropout_ratio_input = dropout_ratio_input
        self.dropout_ratio_hidden = dropout_ratio_hidden
        self.weight_decay = float(weight_decay)

        # Summaries for TensorBoard
        self.summaries_train = []
        self.summaries_dev = []

        self.name = name

    def _add_gaussian_noise_to_inputs(self, inputs, stddev=0.075):
        """Add gaussian noise to the inputs.
        Args:
            inputs: the noise free input-features.
            stddev: The standart deviation of the noise.
        Returns:
            inputs: Input features plus noise.
        """
        if stddev != 0:
            with tf.variable_scope("input_noise"):
                # Add input noise with a standart deviation of stddev.
                inputs = tf.random_normal(
                    tf.shape(inputs), 0.0, stddev) + inputs
        return inputs

    def _add_noise_to_gradients(grads_and_vars, gradient_noise_scale,
                                stddev=0.075):
        """Adds scaled noise from a 0-mean normal distribution to gradients."""
        raise NotImplementedError

    def compute_loss(self, inputs, labels, inputs_seq_len,
                     num_gpu=1, scope=None):
        """Operation for computing ctc loss.
        Args:
            inputs: A tensor of size `[batch_size, max_time, input_size]`
            labels: A SparseTensor of target labels
            inputs_seq_len: A tensor of size `[batch_size]`
            num_gpu: the number of GPUs
        Returns:
            loss: operation for computing ctc loss
            logits:
        """
        # Build model graph
        logits = self._build(inputs, inputs_seq_len)

        # Weight decay
        with tf.name_scope("weight_decay_loss"):
            weight_sum = 0
            for var in tf.trainable_variables():
                if 'bias' not in var.name.lower():
                    weight_sum += tf.nn.l2_loss(var)
            tf.add_to_collection('losses', weight_sum * self.weight_decay)

        with tf.name_scope("ctc_loss"):
            ctc_loss = tf.nn.ctc_loss(labels,
                                      logits,
                                      tf.cast(inputs_seq_len, tf.int32))
            ctc_loss_mean = tf.reduce_mean(ctc_loss, name='ctc_loss_mean')
            tf.add_to_collection('losses', ctc_loss_mean)

        # Compute total loss
        loss = tf.add_n(tf.get_collection('losses'), name='total_loss')

        if num_gpu == 1:
            # Add a scalar summary for the snapshot of loss
            with tf.name_scope("total_loss"):
                self.summaries_train.append(
                    tf.summary.scalar('loss_train', loss))
                self.summaries_dev.append(
                    tf.summary.scalar('loss_dev', loss))

        return loss, logits

    def train(self, loss, optimizer, learning_rate_init=None,
              clip_grad_by_norm=None, is_scheduled=False):
        """Operation for training.
        Args:
            loss: An operation for computing loss
            optimizer: string, name of the optimizer in OPTIMIZER_CLS_NAMES
            learning_rate_init: initial learning rate
            clip_grad_by_norm: if True, clip gradients by norm of the
                value of self.clip_grad
            is_scheduled: if True, schedule learning rate at each epoch
        Returns:
            train_op: operation for training
        """
        optimizer = optimizer.lower()
        if optimizer not in OPTIMIZER_CLS_NAMES:
            raise ValueError(
                "Optimizer name should be one of [%s], you provided %s." %
                (", ".join(OPTIMIZER_CLS_NAMES), optimizer))
        if learning_rate_init < 0.0:
            raise ValueError("Invalid learning_rate %s.", learning_rate_init)

        self.lr = tf.placeholder(tf.float32, name='learning_rate')

        # Select optimizer
        if is_scheduled:
            learning_rate_init = self.lr

        if optimizer == 'momentum':
            optimizer = OPTIMIZER_CLS_NAMES[optimizer](
                learning_rate=learning_rate_init,
                momentum=0.9)
        else:
            optimizer = OPTIMIZER_CLS_NAMES[optimizer](
                learning_rate=learning_rate_init)

        # Create a variable to track the global step
        global_step = tf.Variable(0, name='global_step', trainable=False)

        if self.clip_grad is not None:
            # Gradient clipping
            train_op = self._gradient_clipping(loss,
                                               optimizer,
                                               clip_grad_by_norm,
                                               global_step)

            # TODO: Optionally add noise to weight matrix when training
            # どっちが先？

        else:
            # Use the optimizer to apply the gradients that minimize the loss
            # and also increment the global step counter as a single training
            # step
            train_op = optimizer.minimize(loss, global_step=global_step)

        return train_op

    def _gradient_clipping(self, loss, optimizer, clip_grad_by_norm,
                           global_step):
        print('--- Apply gradient clipping ---')
        # Compute gradients
        trainable_vars = tf.trainable_variables()
        grads = tf.gradients(loss, trainable_vars)

        if clip_grad_by_norm:
            # Clip by norm
            self.clipped_grads = [tf.clip_by_norm(
                g,
                clip_norm=self.clip_grad) for g in grads]
        else:
            # Clip by absolute values
            self.clipped_grads = [tf.clip_by_value(
                g,
                clip_value_min=-self.clip_grad,
                clip_value_max=self.clip_grad) for g in grads]

        # TODO: Add histograms for variables, gradients (norms)
        # self._tensorboard_statistics(trainable_vars)

        # Create gradient updates
        train_op = optimizer.apply_gradients(
            zip(self.clipped_grads, trainable_vars),
            global_step=global_step,
            name='train')

        return train_op

    def decoder(self, logits, inputs_seq_len, decode_type, beam_width=None):
        """Operation for decoding.
        Args:
            logits:
            inputs_seq_len: A tensor of size `[batch_size]`
            decode_type: greedy or beam_search
            beam_width: beam width for beam search
        Return:
            decode_op: A SparseTensor
        """
        if decode_type not in ['greedy', 'beam_search']:
            raise ValueError('decode_type is "greedy" or "beam_search".')

        if decode_type == 'greedy':
            decoded, _ = tf.nn.ctc_greedy_decoder(
                logits, tf.cast(inputs_seq_len, tf.int32))

        elif decode_type == 'beam_search':
            if beam_width is None:
                raise ValueError('Set beam_width.')

            decoded, _ = tf.nn.ctc_beam_search_decoder(
                logits, tf.cast(inputs_seq_len, tf.int32),
                beam_width=beam_width)

        decode_op = tf.to_int32(decoded[0])

        return decode_op

    def posteriors(self, logits):
        """Operation for computing posteriors of each time steps.
        Args:
            logits:
        Return:
            posteriors_op: operation for computing posteriors for each class
        """
        # logits_3d : (max_time, batch_size, num_classes)
        logits_2d = tf.reshape(logits, [-1, self.num_classes])
        posteriors_op = tf.nn.softmax(logits_2d)

        return posteriors_op

    def compute_ler(self, decode_op, labels):
        """Operation for computing LER (Label Error Rate).
        Args:
            decode_op: operation for decoding
            labels: A SparseTensor of target labels
        Return:
            ler_op: operation for computing LER
        """
        # Compute LER (normalize by label length)
        ler_op = tf.reduce_mean(tf.edit_distance(
            decode_op, labels, normalize=True))
        # NOTE: ここでの編集距離はラベルだから，文字に変換しないと正しいCERは得られない

        # Add a scalar summary for the snapshot of LER
        with tf.name_scope("ler"):
            self.summaries_train.append(tf.summary.scalar(
                'ler_train', ler_op))
            self.summaries_dev.append(tf.summary.scalar(
                'ler_dev', ler_op))

        return ler_op

    def _tensorboard_statistics(self, trainable_vars):
        """Compute statistics for TensorBoard plot.
        Args:
            trainable_vars:
        """
        # Histogram
        with tf.name_scope("train"):
            for var in trainable_vars:
                self.summaries_train.append(
                    tf.summary.histogram(var.name, var))
        with tf.name_scope("dev"):
            for var in trainable_vars:
                self.summaries_dev.append(
                    tf.summary.histogram(var.name, var))

        # Mean
        with tf.name_scope("mean_train"):
            for var in trainable_vars:
                self.summaries_train.append(
                    tf.summary.scalar(var.name,
                                      tf.reduce_mean(var)))
        with tf.name_scope("mean_dev"):
            for var in trainable_vars:
                self.summaries_dev.append(
                    tf.summary.scalar(var.name,
                                      tf.reduce_mean(var)))

        # Standard deviation
        with tf.name_scope("stddev_train"):
            for var in trainable_vars:
                self.summaries_train.append(
                    tf.summary.scalar(var.name, tf.sqrt(
                        tf.reduce_mean(tf.square(var - tf.reduce_mean(var))))))
        with tf.name_scope("stddev_dev"):
            for var in trainable_vars:
                self.summaries_dev.append(
                    tf.summary.scalar(var.name, tf.sqrt(
                        tf.reduce_mean(tf.square(var - tf.reduce_mean(var))))))

        # Max
        with tf.name_scope("max_train"):
            for var in trainable_vars:
                self.summaries_train.append(
                    tf.summary.scalar(var.name,
                                      tf.reduce_max(var)))
        with tf.name_scope("max_dev"):
            for var in trainable_vars:
                self.summaries_dev.append(
                    tf.summary.scalar(var.name, tf.reduce_max(var)))

        # Min
        with tf.name_scope("min_train"):
            for var in trainable_vars:
                self.summaries_train.append(
                    tf.summary.scalar(var.name,
                                      tf.reduce_min(var)))
        with tf.name_scope("min_dev"):
            for var in trainable_vars:
                self.summaries_dev.append(
                    tf.summary.scalar(var.name,
                                      tf.reduce_min(var)))
