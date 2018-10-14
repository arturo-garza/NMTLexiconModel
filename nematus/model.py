import sys
import logging

import numpy
import tensorflow as tf

import inference
import layers


class Decoder(object):
    def __init__(self, config, context, x_mask,
                 dropout_target, dropout_embedding, dropout_hidden,
                 encoder_embedding_layer=None, src_embs = None, lexical_model = None):

        self.dropout_target = dropout_target
        batch_size = tf.shape(x_mask)[1]
        
        if hasattr(config, 'lexical'):
            self.lexical = config.lexical
            self.src_embs=src_embs
            with tf.name_scope("lexical"):
                self.lexical_model=lexical_model
        else:
           self.lexical = None
        
        with tf.variable_scope("initial_state_constructor"):
            context_sum = tf.reduce_sum(
                            context * tf.expand_dims(x_mask, axis=2),
                            axis=0)

            context_mean = context_sum / tf.expand_dims(
                                            tf.reduce_sum(x_mask, axis=0),
                                            axis=1)
            self.init_state_layer = layers.FeedForwardLayer(
                                        in_size=config.state_size * 2,
                                        out_size=config.state_size,
                                        batch_size=batch_size,
                                        use_layer_norm=config.use_layer_norm,
                                        dropout_input=dropout_hidden)
            self.init_state = self.init_state_layer.forward(context_mean)

            self.translation_maxlen = config.translation_maxlen
            self.embedding_size = config.target_embedding_size
            self.state_size = config.state_size
            self.target_vocab_size = config.target_vocab_size

        with tf.variable_scope("embedding"):
            if encoder_embedding_layer == None:
                self.y_emb_layer = layers.EmbeddingLayer(
                    vocabulary_sizes=[config.target_vocab_size],
                    dim_per_factor=[config.target_embedding_size])
            else:
                self.y_emb_layer = encoder_embedding_layer

        with tf.variable_scope("base"):
            with tf.variable_scope("gru0"):
                if config.theano_compat:
                    bias_type = layers.LegacyBiasType.THEANO
                else:
                    bias_type = layers.LegacyBiasType.NEMATUS_COMPAT_FALSE
                self.grustep1 = layers.GRUStep(
                                    input_size=config.target_embedding_size,
                                    state_size=config.state_size,
                                    batch_size=batch_size,
                                    use_layer_norm=config.use_layer_norm,
                                    legacy_bias_type=bias_type,
                                    dropout_input=dropout_embedding,
                                    dropout_state=dropout_hidden)
            with tf.variable_scope("attention"):
                self.attstep = layers.AttentionStep(
                                context=context,
                                context_state_size=2*config.state_size,
                                context_mask=x_mask,
                                state_size=config.state_size,
                                hidden_size=2*config.state_size,
                                use_layer_norm=config.use_layer_norm,
                                dropout_context=dropout_hidden,
                                dropout_state=dropout_hidden)
            if config.theano_compat:
                bias_type = layers.LegacyBiasType.THEANO
            else:
                bias_type = layers.LegacyBiasType.NEMATUS_COMPAT_TRUE
            self.grustep2 = layers.DeepTransitionGRUStep(
                                    input_size=2*config.state_size,
                                    state_size=config.state_size,
                                    batch_size=batch_size,
                                    use_layer_norm=config.use_layer_norm,
                                    legacy_bias_type=bias_type,
                                    dropout_input=dropout_hidden,
                                    dropout_state=dropout_hidden,
                                    transition_depth=config.dec_base_recurrence_transition_depth-1,
                                    var_scope_fn=lambda i: "gru{0}".format(i+1))

        with tf.variable_scope("high"):
            if config.dec_depth == 1:
                self.high_gru_stack = None
            else:
                if config.theano_compat:
                    bias_type = layers.LegacyBiasType.THEANO
                else:
                    bias_type = layers.LegacyBiasType.NEMATUS_COMPAT_TRUE
                self.high_gru_stack = layers.GRUStack(
                    input_size=config.state_size,
                    state_size=config.state_size,
                    batch_size=batch_size,
                    use_layer_norm=config.use_layer_norm,
                    legacy_bias_type=bias_type,
                    dropout_input=dropout_hidden,
                    dropout_state=dropout_hidden,
                    stack_depth=config.dec_depth-1,
                    transition_depth=config.dec_high_recurrence_transition_depth,
                    context_state_size=(2*config.state_size if config.dec_deep_context else 0),
                    residual_connections=True,
                    first_residual_output=0)

        with tf.variable_scope("next_word_predictor"):
            W = None
            if config.tie_decoder_embeddings:
                W = self.y_emb_layer.get_embeddings(factor=0)
                W = tf.transpose(W)
            self.predictor = Predictor(config, batch_size, dropout_embedding,
                                       dropout_hidden, hidden_to_logits_W=W)


    def sample(self):
        batch_size = tf.shape(self.init_state)[0]
        high_depth = 0 if self.high_gru_stack == None \
                       else len(self.high_gru_stack.grus)
        i = tf.constant(0)
        init_y = -tf.ones(dtype=tf.int32, shape=[batch_size])
        init_emb = tf.zeros(dtype=tf.float32,
                            shape=[batch_size,self.embedding_size])
        y_array = tf.TensorArray(
            dtype=tf.int32,
            size=self.translation_maxlen,
            clear_after_read=True, #TODO: does this help? or will it only introduce bugs in the future?
            name='y_sampled_array')
        init_loop_vars = [i, self.init_state, [self.init_state] * high_depth,
                          init_y, init_emb, y_array]

        def cond(i, base_state, high_states, prev_y, prev_emb, y_array):
            return tf.logical_and(
                tf.less(i, self.translation_maxlen),
                tf.reduce_any(tf.not_equal(prev_y, 0)))

        def body(i, prev_base_state, prev_high_states, prev_y, prev_emb,
                 y_array):
            state1 = self.grustep1.forward(prev_base_state, prev_emb)
            att_ctx, scores = self.attstep.forward(state1)
            if self.lexical:
                c_embed = self.lexical_model.calc_c_embed(self.src_embs, scores)
            base_state = self.grustep2.forward(state1, att_ctx)
            if self.high_gru_stack == None:
                output = base_state
                high_states = []
            else:
                if self.high_gru_stack.context_state_size == 0:
                    output, high_states = self.high_gru_stack.forward_single(
                        prev_high_states, base_state)
                else:
                    output, high_states = self.high_gru_stack.forward_single(
                        prev_high_states, base_state, context=att_ctx)
            if self.lexical:
                logits, lex_logits = self.predictor.get_logits(prev_emb, output, att_ctx,
                                    c_embed, self.lexical_model ,multi_step=False)
            else:
                logits = self.predictor.get_logits(prev_emb, output, att_ctx,
                                                multi_step=False)
            new_y = tf.multinomial(logits, num_samples=1)
            new_y = tf.cast(new_y, dtype=tf.int32)
            new_y = tf.squeeze(new_y, axis=1)
            new_y = tf.where(tf.equal(prev_y, tf.constant(0, dtype=tf.int32)),
                             tf.zeros_like(new_y), new_y)
            y_array = y_array.write(index=i, value=new_y)
            new_emb = self.y_emb_layer.forward(new_y, factor=0)
            return i+1, base_state, high_states, new_y, new_emb, y_array

        final_loop_vars = tf.while_loop(
                           cond=cond,
                           body=body,
                           loop_vars=init_loop_vars,
                           back_prop=False)
        i, _, _, _, _, y_array = final_loop_vars
        sampled_ys = y_array.gather(tf.range(0, i))
        return sampled_ys

    def score(self, y):
        with tf.variable_scope("y_embeddings_layer"):
            y_but_last = tf.slice(y, [0,0], [tf.shape(y)[0]-1, -1])
            y_embs = self.y_emb_layer.forward(y_but_last, factor=0)
            if self.dropout_target != None:
                y_embs = self.dropout_target(y_embs)
            y_embs = tf.pad(y_embs,
                            mode='CONSTANT',
                            paddings=[[1,0],[0,0],[0,0]]) # prepend zeros
        attn_x = tf.placeholder(dtype=tf.float32, shape = (None, None, 1))
        attention_out = tf.placeholder(dtype=tf.float32, shape = (None, None, 1))
        init_attended_context = tf.zeros([tf.shape(self.init_state)[0], self.state_size*2])
        init_state_att_ctx = (self.init_state, init_attended_context, attention_out)
        gates_x, proposal_x = self.grustep1.precompute_from_x(y_embs)
        def step_fn(prev, x):
            prev_state = prev[0]
            prev_att_ctx = prev[1]
            prev_attention = prev[2]
            gates_x2d = x[0]
            proposal_x2d = x[1]
            attent = x[2]
            state = self.grustep1.forward(
                        prev_state,
                        gates_x=gates_x2d,
                        proposal_x=proposal_x2d)
            att_ctx, scores = self.attstep.forward(state)
            state = self.grustep2.forward(state, att_ctx)
            #TODO: write att_ctx to tensorArray instead of having it as output of scan?
            return (state, att_ctx, scores)

        layer = layers.RecurrentLayer(initial_state=init_state_att_ctx,
                                      step_fn=step_fn)
        states, attended_states, scores = layer.forward((gates_x, proposal_x, attn_x))

        if self.high_gru_stack != None:
            states = self.high_gru_stack.forward(
                states,
                context_layer=(attended_states if self.high_gru_stack.context_state_size > 0 else None))

        if self.lexical:
            c_embed = self.lexical_model.calc_c_embed(self.src_embs, scores)
            logits, lex_logits = self.predictor.get_logits(y_embs, states, attended_states, c_embed, self.lexical_model, multi_step=True)
            return logits, lex_logits
        else:
            logits = self.predictor.get_logits(y_embs, states, attended_states, multi_step=True)
            return logits

class Predictor(object):
    def __init__(self, config, batch_size, dropout_embedding, dropout_hidden, hidden_to_logits_W=None):
        self.config = config

        with tf.variable_scope("prev_emb_to_hidden"):
            self.prev_emb_to_hidden = layers.FeedForwardLayer(
                                in_size=config.target_embedding_size,
                                out_size=config.target_embedding_size,
                                batch_size=batch_size,
                                non_linearity=lambda y: y,
                                use_layer_norm=config.use_layer_norm,
                                dropout_input=dropout_embedding)
        with tf.variable_scope("state_to_hidden"):
            self.state_to_hidden = layers.FeedForwardLayer(
                                    in_size=config.state_size,
                                    out_size=config.target_embedding_size,
                                    batch_size=batch_size,
                                    non_linearity=lambda y: y,
                                    use_layer_norm=config.use_layer_norm,
                                    dropout_input=dropout_hidden)
        with tf.variable_scope("attended_context_to_hidden"):
            self.att_ctx_to_hidden = layers.FeedForwardLayer(
                                    in_size=2*config.state_size,
                                    out_size=config.target_embedding_size,
                                    batch_size=batch_size,
                                    non_linearity=lambda y: y,
                                    use_layer_norm=config.use_layer_norm,
                                    dropout_input=dropout_hidden)

        if config.output_hidden_activation == 'prelu':
            with tf.variable_scope("hidden_prelu"):
                self.hidden_prelu = PReLU(in_size=config.target_embedding_size)

        with tf.variable_scope("hidden_to_logits"):
            self.hidden_to_logits = layers.FeedForwardLayer(
                            in_size=config.target_embedding_size,
                            out_size=config.target_vocab_size,
                            batch_size=batch_size,
                            non_linearity=lambda y: y,
                            W=hidden_to_logits_W,
                            dropout_input=dropout_embedding)

        if config.softmax_mixture_size > 1:
            with tf.variable_scope("hidden_to_pi_logits"):
                self.hidden_to_pi_logits = layers.FeedForwardLayer(
                    in_size=config.target_embedding_size,
                    out_size=config.softmax_mixture_size,
                    batch_size=batch_size,
                    non_linearity=lambda y: y,
                    dropout_input=dropout_embedding)
            self.hidden_to_mos_hidden = []
            for k in range(config.softmax_mixture_size):
                with tf.variable_scope("hidden_to_mos_hidden_{}".format(k)):
                    layer = layers.FeedForwardLayer(
                        in_size=config.target_embedding_size,
                        out_size=config.target_embedding_size,
                        batch_size=batch_size,
                        use_layer_norm=config.use_layer_norm,
                        dropout_input=dropout_embedding)
                    self.hidden_to_mos_hidden.append(layer)

    def get_logits(self, y_embs, states, attended_states, c_embed=None, lexical_model=None, multi_step=True):
        with tf.variable_scope("prev_emb_to_hidden"):
            hidden_emb = self.prev_emb_to_hidden.forward(y_embs, input_is_3d=multi_step)

        with tf.variable_scope("state_to_hidden"):
            hidden_state = self.state_to_hidden.forward(states, input_is_3d=multi_step)

        with tf.variable_scope("attended_context_to_hidden"):
            hidden_att_ctx = self.att_ctx_to_hidden.forward(attended_states,input_is_3d=multi_step)

        hidden = hidden_emb + hidden_state + hidden_att_ctx
        
        if self.config.lexical:
            with tf.name_scope("lexical"):
                c_embed = lexical_model.lexical_model.forward(c_embed, input_is_3d=multi_step) + c_embed
            with tf.name_scope("lexical_context_to_logits"):
                lex_logits = lexical_model.lexical_to_logits.forward(c_embed, input_is_3d=multi_step)
        
        if self.config.output_hidden_activation == 'tanh':
            hidden = tf.tanh(hidden)
        elif self.config.output_hidden_activation == 'relu':
            hidden = tf.nn.relu(hidden)
        elif self.config.output_hidden_activation == 'prelu':
            hidden = self.hidden_prelu.forward(hidden)
        elif self.config.output_hidden_activation == 'linear':
            pass
        else:
            assert False, 'Unknown output activation function "%s"' % self.config.output_hidden_activation

        if self.config.softmax_mixture_size == 1:
            with tf.variable_scope("hidden_to_logits"):
                logits = self.hidden_to_logits.forward(hidden, input_is_3d=multi_step)
        else:
            assert self.config.softmax_mixture_size > 1
            pi_logits = self.hidden_to_pi_logits.forward(hidden,
                                                         input_is_3d=multi_step)
            pi = tf.nn.softmax(pi_logits)
            probs = None
            for k in range(self.config.softmax_mixture_size):
                hidden_k = self.hidden_to_mos_hidden[k].forward(hidden,
                    input_is_3d=multi_step)
                logits_k = self.hidden_to_logits.forward(hidden_k,
                    input_is_3d=multi_step)
                probs_k = tf.nn.softmax(logits_k)
                weight = pi[..., k:k+1]
                if k == 0:
                    probs = probs_k * weight
                else:
                    probs += probs_k * weight
            logits = tf.log(probs)

        if self.config.lexical:
            return logits, lex_logits
        else:
            return logits


class Encoder(object):
    def __init__(self, config, batch_size, dropout_source, dropout_embedding,
                 dropout_hidden):

        self.dropout_source = dropout_source

        with tf.variable_scope("embedding"):
            self.emb_layer = layers.EmbeddingLayer(config.source_vocab_sizes,
                                                   config.dim_per_factor)

        if config.theano_compat:
            bias_type = layers.LegacyBiasType.THEANO
        else:
            bias_type = layers.LegacyBiasType.NEMATUS_COMPAT_FALSE

        with tf.variable_scope("forward-stack"):
            self.forward_encoder = layers.GRUStack(
                    input_size=config.embedding_size,
                    state_size=config.state_size,
                    batch_size=batch_size,
                    use_layer_norm=config.use_layer_norm,
                    legacy_bias_type=bias_type,
                    dropout_input=dropout_embedding,
                    dropout_state=dropout_hidden,
                    stack_depth=config.enc_depth,
                    transition_depth=config.enc_recurrence_transition_depth,
                    alternating=True,
                    residual_connections=True,
                    first_residual_output=1)

        with tf.variable_scope("backward-stack"):
            self.backward_encoder = layers.GRUStack(
                    input_size=config.embedding_size,
                    state_size=config.state_size,
                    batch_size=batch_size,
                    use_layer_norm=config.use_layer_norm,
                    legacy_bias_type=bias_type,
                    dropout_input=dropout_embedding,
                    dropout_state=dropout_hidden,
                    stack_depth=config.enc_depth,
                    transition_depth=config.enc_recurrence_transition_depth,
                    alternating=True,
                    reverse_alternation=True,
                    residual_connections=True,
                    first_residual_output=1)

    def get_src_embeddings(self):
        return self.embs
    def get_context(self, x, x_mask):

        with tf.variable_scope("embedding"):
            embs = self.emb_layer.forward(x)
            if self.dropout_source != None:
                embs = self.dropout_source(embs)
            self.embs = embs
        with tf.variable_scope("forward-stack"):
            fwd_states = self.forward_encoder.forward(embs, x_mask)

        with tf.variable_scope("backward-stack"):
            bwd_states = self.backward_encoder.forward(embs, x_mask)

        # Concatenate the left-to-right and the right-to-left states, in that
        # order. This is for compatibility with models that were trained with
        # the Theano version.
        stack_depth = len(self.forward_encoder.grus)
        if stack_depth % 2 == 0:
            concat_states = tf.concat([bwd_states, fwd_states], axis=2)
        else:
            concat_states = tf.concat([fwd_states, bwd_states], axis=2)
        return concat_states


class ModelInputs(object):
    def __init__(self, config):
        # variable dimensions
        seq_len, batch_size = None, None

        self.x = tf.placeholder(
            name='x',
            shape=(config.factors, seq_len, batch_size),
            dtype=tf.int32)

        self.x_mask = tf.placeholder(
            name='x_mask',
            shape=(seq_len, batch_size),
            dtype=tf.float32)

        self.y = tf.placeholder(
            name='y',
            shape=(seq_len, batch_size),
            dtype=tf.int32)

        self.y_mask = tf.placeholder(
            name='y_mask',
            shape=(seq_len, batch_size),
            dtype=tf.float32)

        self.training = tf.placeholder_with_default(
            False,
            name='training',
            shape=())

class LexicalModel(object):
    def __init__(self, config, batch_size, dropout_source, dropout_embedding,
                 dropout_hidden):
        self.config=config
        with tf.variable_scope("lexical_model"):
            self.lexical_model =  layers.FeedForwardLayer(in_size=config.embedding_size,
                                                   out_size=config.state_size,
                                                   batch_size=batch_size,
                                                   non_linearity=tf.nn.tanh)
        with tf.variable_scope("lexical_context_to_logits"):
            self.lexical_to_logits = layers.FeedForwardLayer(
                                                      in_size=config.embedding_size,
                                                      out_size=config.target_vocab_size,
                                                      batch_size=batch_size,
                                                      use_layer_norm=config.use_layer_norm,
                                                      dropout_input=dropout_hidden)
    
    def calc_c_embed(self, src_embs, scores):
        c_embed = tf.multiply(src_embs, scores)
        c_embed = tf.reduce_sum(c_embed, axis=0, keep_dims=False)
        c_embed = tf.tanh(c_embed)
        return c_embed

class StandardModel(object):
    def __init__(self, config):
        self.inputs = ModelInputs(config)

        # Dropout functions for words.
        # These probabilistically zero-out all embedding values for individual
        # words.
        dropout_source, dropout_target = None, None
        if config.use_dropout and config.dropout_source > 0.0:
            def dropout_source(x):
                return tf.layers.dropout(
                    x, noise_shape=(tf.shape(x)[0], tf.shape(x)[1], 1),
                    rate=config.dropout_source, training=self.inputs.training)
        if config.use_dropout and config.dropout_target > 0.0:
            def dropout_target(y):
                return tf.layers.dropout(
                    y, noise_shape=(tf.shape(y)[0], tf.shape(y)[1], 1),
                    rate=config.dropout_target, training=self.inputs.training)

        # Dropout functions for use within FF, GRU, and attention layers.
        # We use Gal and Ghahramani (2016)-style dropout, so these functions
        # will be used to create 2D dropout masks that are reused at every
        # timestep.
        dropout_embedding, dropout_hidden = None, None
        if config.use_dropout and config.dropout_embedding > 0.0:
            def dropout_embedding(e):
                return tf.layers.dropout(e, noise_shape=tf.shape(e),
                                         rate=config.dropout_embedding,
                                         training=self.inputs.training)
        if config.use_dropout and config.dropout_hidden > 0.0:
            def dropout_hidden(h):
                return tf.layers.dropout(h, noise_shape=tf.shape(h),
                                         rate=config.dropout_hidden,
                                         training=self.inputs.training)

        batch_size = tf.shape(self.inputs.x)[-1]  # dynamic value

        with tf.variable_scope("encoder"):
            self.encoder = Encoder(config, batch_size, dropout_source,
                                   dropout_embedding, dropout_hidden)
            ctx = self.encoder.get_context(self.inputs.x, self.inputs.x_mask)
            src_embs=self.encoder.get_src_embeddings()
            
        if config.lexical:
            logging.info('Lexical model enabled...')
            with tf.variable_scope("lexical"):
                self.lexical_model = LexicalModel(config, batch_size, dropout_source,
                                                  dropout_embedding, dropout_hidden)
            with tf.variable_scope("decoder"):
                if config.tie_encoder_decoder_embeddings:
                    tied_embeddings = self.encoder.emb_layer
                else:
                    tied_embeddings = None
                self.decoder = Decoder(config, ctx, self.inputs.x_mask,
                                       dropout_target, dropout_embedding,
                                       dropout_hidden, tied_embeddings,
                                       src_embs, self.lexical_model)
                self.logits, self.lex_logits = self.decoder.score(self.inputs.y)
        else:
            with tf.variable_scope("decoder"):
                if config.tie_encoder_decoder_embeddings:
                    tied_embeddings = self.encoder.emb_layer
                else:
                    tied_embeddings = None
                self.decoder = Decoder(config, ctx, self.inputs.x_mask,
                                       dropout_target, dropout_embedding,
                                       dropout_hidden, tied_embeddings)
                self.logits = self.decoder.score(self.inputs.y)

        with tf.variable_scope("loss"):
            if config.lexical:
                if config.label_smoothing:
                    rnn_uniform_prob = self.smoothing_factor / tf.cast(tf.shape(self.logits)[-1], tf.float32)
                    rnn_smoothed_prob = 1.0-self.smoothing_factor + rnn_uniform_prob
                    rnn_onehot_labels = tf.one_hot(self.inputs.y, tf.shape(self.logits)[-1],
                                                   on_value = rnn_smoothed_prob,
                                                   off_value = rnn_uniform_prob,
                                                   dtype = tf.float32)
                    rnn_cost = tf.losses.softmax_cross_entropy(
                        onehot_labels=rnn_onehot_labels,
                        logits=self.logits,
                        weights=self.inputs.y_mask,
                        reduction=tf.losses.Reduction.NONE)
                    
                    #lex_uniform_prob = self.smoothing_factor / tf.cast(tf.shape(self.lex_logits)[-1], tf.float32)
                    #lex_smoothed_prob = 1.0-self.smoothing_factor + lex_uniform_prob
                    #lex_onehot_labels = tf.one_hot(self.inputs.y, tf.shape(self.lex_logits)[-1],
                    #                               on_value = lex_smoothed_prob,
                    #                               off_value = lex_uniform_prob,
                    #                                dtype = tf.float32)
                    # lex_cost = tf.losses.softmax_cross_entropy(
                    #     onehot_labels=lex_onehot_labels,
                    #     logits=self.lex_logits,
                    #     weights=self.inputs.y_mask,
                    #     reduction=tf.losses.Reduction.NONE)
                else:
                    rnn_cost = tf.losses.sparse_softmax_cross_entropy(
                        labels=self.inputs.y,
                        logits=self.logits,
                        weights=self.inputs.y_mask,
                        reduction=tf.losses.Reduction.NONE)
                    lex_cost = tf.losses.sparse_softmax_cross_entropy(
                        labels=self.inputs.y,
                        logits=self.lex_logits,
                        weights=self.inputs.y_mask,
                        reduction=tf.losses.Reduction.NONE)
                
                cost=rnn_cost+lex_cost
                self.loss_per_sentence = tf.reduce_sum(cost, axis=0, keep_dims=False)
            else:
                self.loss_layer = layers.Masked_cross_entropy_loss(
                    self.inputs.y, self.inputs.y_mask, config.label_smoothing,
                    training=self.inputs.training)
                self.loss_per_sentence = self.loss_layer.forward(self.logits)
            
            
            self.objective = tf.reduce_mean(self.loss_per_sentence,
                                            keepdims=False)
            self.l2_loss = tf.constant(0.0, dtype=tf.float32)
            if config.decay_c > 0.0:
                self.l2_loss = tf.add_n([tf.nn.l2_loss(v) for v in tf.trainable_variables()]) * tf.constant(config.decay_c, dtype=tf.float32)
                self.objective += self.l2_loss

            self.map_l2_loss = tf.constant(0.0, dtype=tf.float32)
            if config.map_decay_c > 0.0:
                map_l2_acc = []
                for v in tf.trainable_variables():
                    prior_name = 'prior/'+v.name.split(':')[0]
                    prior_v = tf.get_variable(
                        prior_name, initializer=v.initialized_value(),
                        trainable=False, collections=['prior_variables'],
                        dtype=v.initialized_value().dtype)
                    map_l2_acc.append(tf.nn.l2_loss(v - prior_v))
                self.map_l2_loss = tf.add_n(map_l2_acc) * tf.constant(config.map_decay_c, dtype=tf.float32)
                self.objective += self.map_l2_loss

        self.sampled_ys = None
        self.beam_size, self.beam_ys, self.parents, self.cost = None, None, None, None

    def get_loss(self):
        return self.loss_per_sentence

    def get_objective(self):
        return self.objective

    def _get_samples(self):
        if self.sampled_ys == None:
            self.sampled_ys = self.decoder.sample()
        return self.sampled_ys

    def sample(self, session, x_in, x_mask_in):
        sampled_ys = self._get_samples()
        feeds = {self.inputs.x: x_in, self.inputs.x_mask: x_mask_in}
        sampled_ys_out = session.run(sampled_ys, feed_dict=feeds)
        sampled_ys_out = sampled_ys_out.T
        samples = []
        for sample in sampled_ys_out:
            sample = numpy.trim_zeros(list(sample), trim='b')
            sample.append(0)
            samples.append(sample)
        return samples

    def _get_beam_search_outputs(self, beam_size):
        if beam_size != self.beam_size:
            self.beam_size = beam_size
            self.beam_ys, self.parents, self.cost = inference.construct_beam_search_functions([self], beam_size)
        return self.beam_ys, self.parents, self.cost


    def beam_search(self, session, x_in, x_mask_in, beam_size):
        # x_in is a numpy array with shape (factors, seqLen, batch)
        # x_mask is a numpy array with shape (seqLen, batch)
        x_in = numpy.repeat(x_in, repeats=beam_size, axis=-1)
        x_mask_in = numpy.repeat(x_mask_in, repeats=beam_size, axis=-1)
        feeds = {self.inputs.x: x_in, self.inputs.x_mask: x_mask_in}
        beam_ys, parents, cost = self._get_beam_search_outputs(beam_size)
        beam_ys_out, parents_out, cost_out = session.run(
                                                    [beam_ys, parents, cost],
                                                    feed_dict=feeds)
        return inference.reconstruct_hypotheses(beam_ys_out, parents_out, cost_out, beam_size)
