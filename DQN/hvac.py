#!/usr/bin/env python
import rospy
# import gym
# from gym.wrappers import Monitor
import itertools
import numpy as np
import os
import random
import sys
import psutil
import tensorflow as tf
import matlab.engine
from geometry_msgs.msg import Twist

if "../" not in sys.path:
  sys.path.append("../")

from lib import plotting
from collections import deque, namedtuple
# env = gym.envs.make("Breakout-v0")
# Atari Actions: 0 (noop), 1 (fire), 2 (left) and 3 (right) are valid actions
VALID_ACTIONS = [0, 1, 2, 3]
'''
class StateProcessor():
    """
    Processes a raw Atari images. Resizes it and converts it to grayscale.
    """
    def __init__(self):
        # Build the Tensorflow graph
        with tf.variable_scope("state_processor"):
            self.input_state = tf.placeholder(shape=[210, 160, 3], dtype=tf.uint8)
            self.output = tf.image.rgb_to_grayscale(self.input_state)
            self.output = tf.image.crop_to_bounding_box(self.output, 34, 0, 160, 160)
            self.output = tf.image.resize_images(
                self.output, [84, 84], method=tf.image.ResizeMethod.NEAREST_NEIGHBOR)
            self.output = tf.squeeze(self.output)

    def process(self, sess, state):
        """
        Args:
            sess: A Tensorflow session object
            state: A [210, 160, 3] Atari RGB State

        Returns:
            A processed [84, 84] state representing grayscale values.
        """
        return sess.run(self.output, { self.input_state: state })
'''
global t_curr
global t_out
global time_clock
global cost 
global curr_state
global prev_state 

def env_node():
    rospy.init_node('hvac_control', anonymous=True)
    rospy.Subscriber("state", Twist, callback)
    pub = rospy.Publisher('flow', Point, queue_size=10)

def callback(data):
    t_curr=data.linear.x
    t_out=data.linear.y
    cost=data.linear.z
    time_clocks=data.angular.x

def state_update():
	curr_state[0]=t_curr
	curr_state[1]=t_out
	curr_state[2]=time_clock
	return curr_state

class Estimator():
    """Q-Value Estimator neural network.

    This network is used for both the Q-Network and the Target Network.
    """

    def __init__(self, scope="estimator", summaries_dir=None):
        self.scope = scope
        # Writes Tensorboard summaries to disk
        self.summary_writer = None
        with tf.variable_scope(scope):
            # Build the graph
            self._build_model()
            if summaries_dir:
                summary_dir = os.path.join(summaries_dir, "summaries_{}".format(scope))
                if not os.path.exists(summary_dir):
                    os.makedirs(summary_dir)
                self.summary_writer = tf.summary.FileWriter(summary_dir)

    def _build_model(self):
        """
        Builds the Tensorflow graph.
        """

        # Placeholders for our input
        # Our input are 4 RGB frames of shape 160, 160 each
        self.X_pl = tf.placeholder(shape=[None, 1, 3 ], dtype=tf.float32, name="X")
        # The TD target value
        self.y_pl = tf.placeholder(shape=[None], dtype=tf.float32, name="y")
        # Integer id of which action was selected
        self.actions_pl = tf.placeholder(shape=[None], dtype=tf.int32, name="actions")

        X = tf.to_float(self.X_pl) 
        batch_size = tf.shape(self.X_pl)[0]

        #lay1 = tf.contrib.fully_connected(X, 50, activation_fn=tf.nn.relu)
        #lay2 = tf.contrib.fully_connected(lay1, 50, activation_fn=tf.nn.relu)

        # self.X_pl = tf.placeholder(shape=[None, 84, 84, 4], dtype=tf.uint8, name="X")
        # # The TD target value
        # self.y_pl = tf.placeholder(shape=[None], dtype=tf.float32, name="y")
        # # Integer id of which action was selected
        # self.actions_pl = tf.placeholder(shape=[None], dtype=tf.int32, name="actions")

        # X = tf.to_float(self.X_pl) / 255.0
        # batch_size = tf.shape(self.X_pl)[0]

        # # Three convolutional layers
        # conv1 = tf.contrib.layers.conv2d(
        #     X, 32, 8, 4, activation_fn=tf.nn.relu)
        # conv2 = tf.contrib.layers.conv2d(
        #     conv1, 64, 4, 2, activation_fn=tf.nn.relu)
        # conv3 = tf.contrib.layers.conv2d(
        #     conv2, 64, 3, 1, activation_fn=tf.nn.relu)

        # Fully connected layers
        #flattened = tf.contrib.layers.flatten(conv3)
        #fc1 = tf.contrib.layers.fully_connected(lay2, 512)
        self.predictions = tf.contrib.layers.fully_connected(X, len(VALID_ACTIONS), activation_fn=tf.nn.relu)

        # Get the predictions for the chosen actions only
        gather_indices = tf.range(batch_size) * tf.shape(self.predictions)[1] + self.actions_pl
        self.action_predictions = tf.gather(tf.reshape(self.predictions, [-1]), gather_indices)

        # Calculate the loss
        self.losses = tf.squared_difference(self.y_pl, self.action_predictions)
        self.loss = tf.reduce_mean(self.losses)

        # Optimizer Parameters from original paper
        self.optimizer = tf.train.RMSPropOptimizer(0.00025, 0.99, 0.0, 1e-6) #learning_rate,decay,momentum,epsilon,
        self.train_op = self.optimizer.minimize(self.loss, global_step=tf.contrib.framework.get_global_step())

        # Summaries for Tensorboard
        self.summaries = tf.summary.merge([
            tf.summary.scalar("loss", self.loss),
            tf.summary.histogram("loss_hist", self.losses),
            tf.summary.histogram("q_values_hist", self.predictions),
            tf.summary.scalar("max_q_value", tf.reduce_max(self.predictions))
        ])

    def predict(self, sess, s):
        """
        Predicts action values.

        Args:
          sess: Tensorflow session
          s: State input of shape [batch_size, 4, 160, 160, 3]

        Returns:
          Tensor of shape [batch_size, NUM_VALID_ACTIONS] containing the estimated 
          action values.
        """
        return sess.run(self.predictions, { self.X_pl: s })

    def update(self, sess, s, a, y):
        """
        Updates the estimator towards the given targets.

        Args:
          sess: Tensorflow session object
          s: State input of shape [batch_size, 4, 160, 160, 3]
          a: Chosen actions of shape [batch_size]
          y: Targets of shape [batch_size]

        Returns:
          The calculated loss on the batch.
        """
        feed_dict = { self.X_pl: s, self.y_pl: y, self.actions_pl: a }
        summaries, global_step, _, loss = sess.run(
            [self.summaries, tf.contrib.framework.get_global_step(), self.train_op, self.loss],
            feed_dict)
        if self.summary_writer:
            self.summary_writer.add_summary(summaries, global_step)
        return loss
# For Testing....
eng=matlab.engine.start_matlab()
eng.sim("house1")
env_node()
tf.reset_default_graph()
global_step = tf.Variable(0, name="global_step", trainable=False)

e = Estimator(scope="test")
sp = StateProcessor()
with tf.Session() as sess:
    sess.run(tf.global_variables_initializer())
    
    # Example observation batch
    rospy.spinOnce()
    observation = state_update()
    
    observation_p = sp.process(sess, observation)
    observation = np.stack([observation_p] * 4, axis=2)
    observations = np.array([observation] * 2)
    
    # Test Prediction
    print(e.predict(sess, observations))

    # Test training step
    y = np.array([10.0, 10.0])
    a = np.array([1, 3])
    print(e.update(sess, observations, a, y))



