import torch
import numpy as np
import torch.nn as nn
import torch.nn.functional as F
from torch.autograd import Variable
import copy
import math
from torch.nn import init
from torch.functional import norm
import torch

def clones(module, N):
    "Produce N identical layers."
    return nn.ModuleList([copy.deepcopy(module) for _ in range(N)])

 
class LayerNorm(nn.Module):
    "Construct a layernorm module (See citation for details)."
    def __init__(self, features, eps=1e-6):
        super(LayerNorm, self).__init__()
        self.a_2 = nn.Parameter(torch.ones(features))
        self.b_2 = nn.Parameter(torch.zeros(features))
        self.eps = eps

    def forward(self, x):
        mean = x.mean(-1, keepdim=True)
        std = x.std(-1, keepdim=True)
        return self.a_2 * (x - mean) / (std + self.eps) + self.b_2


class SublayerConnection(nn.Module):
    """
    A residual connection followed by a layer norm.
    Note for code simplicity the norm is first as opposed to last.
    """
    def __init__(self, size, dropout):
        super(SublayerConnection, self).__init__()
        self.norm = LayerNorm(size)
        self.dropout = nn.Dropout(dropout)

    def forward(self, x, sublayer):
        "Apply residual connection to any sublayer with the same size."
        return x + self.dropout(sublayer(self.norm(x)))


class Embeddings(nn.Module):
    #(bs,flow,N,seq_len,seq_dim)->(bs,flow,N,seq_len,model_dim)
    def __init__(self,dim_in,dim_d):
        super(Embeddings,self).__init__()
        self.embedding = nn.Linear(dim_in,dim_d)
        self.d_model = dim_d

    def forward(self,x):
        return self.embedding(x.float()) * math.sqrt(self.d_model)


class PositionwiseFeedForward(nn.Module):
    "Implements FFN equation."
    '''
    FCN->ReLU->dropout
    '''
    def __init__(self, d_model, d_ff, dropout=0.1):
        super(PositionwiseFeedForward, self).__init__()
        self.w_1 = nn.Linear(d_model, d_ff)
        self.w_2 = nn.Linear(d_ff, d_model)
        self.dropout = nn.Dropout(dropout)

    def forward(self, x):
        return self.w_2(self.dropout(F.relu(self.w_1(x))))


class PositionalEncoding(nn.Module):
    "Implement the PE function."
    def __init__(self, d_model, dropout, max_len=20):
        super(PositionalEncoding, self).__init__()
        self.dropout = nn.Dropout(p=dropout)
        
        # Compute the positional encodings once in log space.
        pe = torch.zeros(max_len, d_model)
        position = torch.arange(0, max_len).unsqueeze(1)
        div_term = torch.exp(torch.arange(0, d_model, 2) *
                             -(math.log(10000.0) / d_model))
        pe[:, 0::2] = torch.sin(position * div_term)
        pe[:, 1::2] = torch.cos(position * div_term)
        pe = pe.unsqueeze(0)
        self.register_buffer('pe', pe)
        
    def forward(self, x):
        #print(x.shape,self.pe.shape)
        x = x + Variable(self.pe[:, :x.size(-2)], 
                         requires_grad=False)
        return self.dropout(x)


def attention(query, key, value, mask=None, dropout=None):
    "Compute 'Scaled Dot Product Attention'"
    d_k = query.size(-1)
    scores = torch.matmul(query, key.transpose(-2, -1)) \
             / math.sqrt(d_k)
    if mask is not None:
        scores = scores.masked_fill(mask == 0, -1e9)
    p_attn = F.softmax(scores, dim = -1)
    if dropout is not None:
        p_attn = dropout(p_attn)
    return torch.matmul(p_attn, value), p_attn


class MultiHeadedAttention(nn.Module):
    def __init__(self, h, d_model, dropout=0.1):
        "Take in model size and number of heads."
        super(MultiHeadedAttention, self).__init__()
        assert d_model % h == 0
        # We assume d_v always equals d_k
        self.d_k = d_model // h
        self.h = h
        self.linears = clones(nn.Linear(d_model, d_model), 4)
        self.attn = None
        self.dropout = nn.Dropout(p=dropout)
        
    def forward(self, query, key, value, mask=None):
        "Implements Figure 2"
        '''
        input:(N,seq_len,d_model)-->(1):(N,h,seq_len,d_model//h)-->(3):(N,seq_len,d_model)
        '''
        if mask is not None:
            # Same mask applied to all h heads.
            mask = mask.unsqueeze(1)
        
        nbatches,N,seq_len,c = query.shape
        
        # 1) Do all the linear projections in batch from d_model => h x d_k 
        query, key, value = \
            [l(x).view(nbatches, N, -1, self.h, self.d_k).transpose(-2, -3)
             for l, x in zip(self.linears, (query, key, value))]
        
        # 2) Apply attention on all the projected vectors in batch. 
        x, self.attn = attention(query, key, value, mask=mask, 
                                 dropout=self.dropout)
        
        # 3) "Concat" using a view and apply a final linear. 
        x = x.transpose(-2, -3).contiguous() \
             .view(nbatches, N, -1, self.h * self.d_k)
        x = self.linears[-1](x)
        #print("FDDDDDDDD:::",x.shape)
        return  x 

  
class EncoderLayer(nn.Module):
    "Encoder is made up of self-attn and feed forward (defined below)"
    def __init__(self, size, self_attn, feed_forward, dropout):
        super(EncoderLayer, self).__init__()
        self.self_attn = self_attn
        self.feed_forward = feed_forward
        self.sublayer = clones(SublayerConnection(size, dropout), 2)
        self.size = size

    def forward(self, x):
        "Follow Figure 1 (left) for connections."
        '''
        mask is not needed in encoder
        '''
        x = self.sublayer[0](x, lambda x: self.self_attn(x, x, x))
        return self.sublayer[1](x, self.feed_forward)
 

class Encoder(nn.Module):
    "Core encoder is a stack of N layers"
    def __init__(self, layer, N):
        super(Encoder, self).__init__()
        self.layers = clones(layer, N)
        self.norm = LayerNorm(layer.size)

    def forward(self, x):
        "Pass the input (and mask) through each layer in turn."
        for layer in self.layers:
            x = layer(x)
        return self.norm(x)


class DecoderLayer(nn.Module):
    "Decoder is made of self-attn, src-attn, and feed forward (defined below)"
    def __init__(self, size, self_attn, src_attn, feed_forward, dropout):
        super(DecoderLayer, self).__init__()
        self.size = size
        self.self_attn = self_attn
        self.src_attn = src_attn
        self.feed_forward = feed_forward
        self.sublayer = clones(SublayerConnection(size, dropout), 3)
  
    def forward(self, x, memory, tgt_mask):
        "Follow Figure 1 (right) for connections."
        m = memory
        # print("MMMMMEMOER:::",m.shape)
        x = self.sublayer[0](x, lambda x: self.self_attn(x, x, x, tgt_mask))
        # print("MMMMMEMOER:::",x.shape)

        x = self.sublayer[1](x, lambda x: self.src_attn(x, m, m))
        return self.sublayer[2](x, self.feed_forward)

 
class Decoder(nn.Module):
    "Generic N layer decoder with masking."
    def __init__(self, layer, N):
        super(Decoder, self).__init__()
        self.layers = clones(layer, N)
        self.norm = LayerNorm(layer.size)
        
    def forward(self, x, memory, tgt_mask):
        for layer in self.layers:
            x = layer(x, memory, tgt_mask)
        return self.norm(x)


class Generator(nn.Module):
    "Define standard linear + sigmoid generation step."
    def __init__(self, d_model, vocab):
        super(Generator, self).__init__()
        self.proj = nn.Linear(d_model, vocab)

    def forward(self, x):
        #return torch.sigmoid(self.proj(x))
        return self.proj(x)


class EncoderDecoder(nn.Module):
    """
    A standard Encoder-Decoder architecture. Base for this and many 
    other models.
    """
    def __init__(self, encoder, decoder, src_embed, tgt_embed, generator):
        super(EncoderDecoder, self).__init__()
        self.encoder = encoder
        self.decoder = decoder
        self.src_embed = src_embed
        self.tgt_embed = tgt_embed
        self.generator = generator

    def forward(self, src, tgt, tgt_mask=None):
        "Take in and process masked src and target sequences."
        return self.generator(self.decode(self.encode(src),tgt, tgt_mask))
 
    def encode(self, src):
        #print('src',src.shape)
        return self.encoder(self.src_embed(src))

    def decode(self, memory, tgt, tgt_mask=None):
        return self.decoder(self.tgt_embed(tgt), memory, tgt_mask)

class Depth_Pointwise_Conv1d(nn.Module):
    def __init__(self,in_ch,out_ch,k):
        super().__init__()
        if(k==1):
            self.depth_conv=nn.Identity()
        else:
            self.depth_conv=nn.Conv1d(
                in_channels=in_ch,
                out_channels=in_ch,
                kernel_size=k,
                groups=in_ch,
                padding=k//2
                )
        self.pointwise_conv=nn.Conv1d(
            in_channels=in_ch,
            out_channels=out_ch,
            kernel_size=1,
            groups=1
        )
    def forward(self,x):
        out=self.pointwise_conv(self.depth_conv(x))
        return out
    


class MUSEAttention(nn.Module):

    def __init__(self, d_model, d_k, d_v, h,dropout=.1):


        super(MUSEAttention, self).__init__()
        d_model = d_model*3
        self.fc_q = nn.Linear(d_model, h * d_k)
        self.fc_k = nn.Linear(d_model, h * d_k)
        self.fc_v = nn.Linear(d_model, h * d_v)
        self.fc_o = nn.Linear(h * d_v, d_model//3)
        self.dropout = nn.Dropout(dropout)

        self.conv1=Depth_Pointwise_Conv1d(h * d_v, d_model//3,1)
        self.conv3=Depth_Pointwise_Conv1d(h * d_v, d_model//3,3)
        self.conv5=Depth_Pointwise_Conv1d(h * d_v, d_model//3,5)
        self.dy_paras=nn.Parameter(torch.ones(3))
        self.softmax=nn.Softmax(-1)

        self.d_model = d_model
        self.d_k =  d_k
        self.d_v =  d_v
        self.h = h

        self.init_weights()


    def init_weights(self):
        for m in self.modules():
            if isinstance(m, nn.Conv2d):
                init.kaiming_normal_(m.weight, mode='fan_out')
                if m.bias is not None:
                    init.constant_(m.bias, 0)
            elif isinstance(m, nn.BatchNorm2d):
                init.constant_(m.weight, 1)
                init.constant_(m.bias, 0)
            elif isinstance(m, nn.Linear):
                init.normal_(m.weight, std=0.001)
                if m.bias is not None:
                    init.constant_(m.bias, 0)

    def forward(self, queries, keys, values, attention_mask=None, attention_weights=None):

        #Self Attention
        # print("OUT::",out.shape)
        b_s, nq = queries.shape[:2]
        # nq=3
        queries = queries.view(b_s, nq, -1)
        keys = keys.view(b_s, nq, -1)
        values = values.view(b_s, nq, -1)
        
        b_s, nq = queries.shape[:2]

        # print("SHAPE::::",self.fc_q(queries).shape) #[64, 7, 3, 128]
        # print("self.d_k SHAPE::::",self.d_k)
        # print("self.h SHAPE::::",self.h)
        # print("self.d_v SHAPE::::",self.d_v)
        nk = keys.shape[1]

        q = self.fc_q(queries).view(b_s, nq, self.h, self.d_k).permute(0, 2, 1, 3)  # (b_s, h, nq, d_k)
        k = self.fc_k(keys).view(b_s, nk, self.h, self.d_k).permute(0, 2, 3, 1)  # (b_s, h, d_k, nk)
        v = self.fc_v(values).view(b_s, nk, self.h, self.d_v).permute(0, 2, 1, 3)  # (b_s, h, nk, d_v)

        att = torch.matmul(q, k) / np.sqrt(self.d_k)  # (b_s, h, nq, nk)
        if attention_weights is not None:
            att = att * attention_weights
        if attention_mask is not None:
            att = att.masked_fill(attention_mask, -np.inf)
        att = torch.softmax(att, -1)
        att=self.dropout(att)

        out = torch.matmul(att, v).permute(0, 2, 1, 3).contiguous().view(b_s, nq, self.h * self.d_v)  # (b_s, nq, h*d_v)
        # print("OUT::",out.shape)
        out = self.fc_o(out)  # (b_s, nq, d_model)

        v2=v.permute(0,1,3,2).contiguous().view(b_s,-1,nk) #bs,dim,n
        self.dy_paras=nn.Parameter(self.softmax(self.dy_paras))
        out2=self.dy_paras[0]*self.conv1(v2)+self.dy_paras[1]*self.conv3(v2)+self.dy_paras[2]*self.conv5(v2)
        out2=out2.permute(0,2,1) #bs.n.dim

        out=out+out2
        out =  torch.unsqueeze(out,2)
        # print("OUT::",out.shape)

        return out
        
'''

class MUSEAttention(nn.Module):

    def __init__(self, d_model, d_k, d_v, h,dropout=.1):


        super(MUSEAttention, self).__init__()
        d_model = d_model*3
        self.fc_q = nn.Linear(d_model, h * d_k)
        self.fc_k = nn.Linear(d_model, h * d_k)
        self.fc_v = nn.Linear(d_model, h * d_v)
        self.fc_o = nn.Linear(h * d_v, d_model//3)
        self.dropout = nn.Dropout(dropout)

        self.conv1=Depth_Pointwise_Conv1d(h * d_v, d_model//3,1)
        self.conv3=Depth_Pointwise_Conv1d(h * d_v, d_model//3,3)
        self.conv5=Depth_Pointwise_Conv1d(h * d_v, d_model//3,5)
        self.dy_paras=nn.Parameter(torch.ones(3))
        self.softmax=nn.Softmax(-1)

        self.d_model = d_model
        self.d_k =  d_k
        self.d_v =  d_v
        self.h = h

        self.init_weights()


    def init_weights(self):
        for m in self.modules():
            if isinstance(m, nn.Conv2d):
                init.kaiming_normal_(m.weight, mode='fan_out')
                if m.bias is not None:
                    init.constant_(m.bias, 0)
            elif isinstance(m, nn.BatchNorm2d):
                init.constant_(m.weight, 1)
                init.constant_(m.bias, 0)
            elif isinstance(m, nn.Linear):
                init.normal_(m.weight, std=0.001)
                if m.bias is not None:
                    init.constant_(m.bias, 0)

    def forward(self, queries, keys, values, attention_mask=None, attention_weights=None):

        #Self Attention
        # print("OUT::",out.shape)
        b_s, nq = queries.shape[:2]
        # nq=3
        queries = queries.view(b_s, nq, -1)
        keys = keys.view(b_s, nq, -1)
        values = values.view(b_s, nq, -1)
        
        b_s, nq = queries.shape[:2]

        # print("SHAPE::::",self.fc_q(queries).shape) #[64, 7, 3, 128]
        # print("self.d_k SHAPE::::",self.d_k)
        # print("self.h SHAPE::::",self.h)
        # print("self.d_v SHAPE::::",self.d_v)
        nk = keys.shape[1]

        q = self.fc_q(queries).view(b_s, nq, self.h, self.d_k).permute(0, 2, 1, 3)  # (b_s, h, nq, d_k)
        k = self.fc_k(keys).view(b_s, nk, self.h, self.d_k).permute(0, 2, 3, 1)  # (b_s, h, d_k, nk)
        v = self.fc_v(values).view(b_s, nk, self.h, self.d_v).permute(0, 2, 1, 3)  # (b_s, h, nk, d_v)

        att = torch.matmul(q, k) / np.sqrt(self.d_k)  # (b_s, h, nq, nk)
        if attention_weights is not None:
            att = att * attention_weights
        if attention_mask is not None:
            att = att.masked_fill(attention_mask, -np.inf)
        att = torch.softmax(att, -1)
        att=self.dropout(att)

        out = torch.matmul(att, v).permute(0, 2, 1, 3).contiguous().view(b_s, nq, self.h * self.d_v)  # (b_s, nq, h*d_v)
        # print("OUT::",out.shape)
        out = self.fc_o(out)  # (b_s, nq, d_model)

        v2=v.permute(0,1,3,2).contiguous().view(b_s,-1,nk) #bs,dim,n
        self.dy_paras=nn.Parameter(self.softmax(self.dy_paras))
        out2=self.dy_paras[0]*self.conv1(v2)+self.dy_paras[1]*self.conv3(v2)+self.dy_paras[2]*self.conv5(v2)
        out2=out2.permute(0,2,1) #bs.n.dim

        out=out+out2
        out =  torch.unsqueeze(out,2)
        # print("OUT::",out.shape)

        return out

''' 
        
class MUSEAttention1(nn.Module):

    def __init__(self, d_model, d_k, d_v, h,dropout=.1):


        super(MUSEAttention1, self).__init__()
        d_model = d_model
        self.fc_q = nn.Linear(d_model, h * d_k)
        self.fc_k = nn.Linear(d_model, h * d_k)
        self.fc_v = nn.Linear(d_model, h * d_v)
        self.fc_o = nn.Linear(h * d_v, d_model)
        self.dropout = nn.Dropout(dropout)

        self.conv1=Depth_Pointwise_Conv1d(h * d_v, d_model,1)
        self.conv3=Depth_Pointwise_Conv1d(h * d_v, d_model,3)
        self.conv5=Depth_Pointwise_Conv1d(h * d_v, d_model,5)
        self.dy_paras=nn.Parameter(torch.ones(3))
        self.softmax=nn.Softmax(-1)

        self.d_model = d_model
        self.d_k =  d_k
        self.d_v =  d_v
        self.h = h

        self.init_weights()


    def init_weights(self):
        for m in self.modules():
            if isinstance(m, nn.Conv2d):
                init.kaiming_normal_(m.weight, mode='fan_out')
                if m.bias is not None:
                    init.constant_(m.bias, 0)
            elif isinstance(m, nn.BatchNorm2d):
                init.constant_(m.weight, 1)
                init.constant_(m.bias, 0)
            elif isinstance(m, nn.Linear):
                init.normal_(m.weight, std=0.001)
                if m.bias is not None:
                    init.constant_(m.bias, 0)

    def forward(self, queries, keys, values, attention_mask=None, attention_weights=None):

        #Self Attention
        # print("DEEEEEECODER::",queries.shape)
        b_s, nq = queries.shape[:2]
        # nq=3
        queries = queries.view(b_s, nq, -1)
        keys = keys.view(b_s, nq, -1)
        values = values.view(b_s, nq, -1)
        
        b_s, nq = queries.shape[:2]

        # print("SHAPE::::",self.fc_q(queries).shape) #[64, 7, 3, 128]
        # print("self.d_k SHAPE::::",self.d_k)
        # print("self.h SHAPE::::",self.h)
        # print("self.d_v SHAPE::::",self.d_v)
        nk = keys.shape[1]

        q = self.fc_q(queries).view(b_s, nq, self.h, self.d_k).permute(0, 2, 1, 3)  # (b_s, h, nq, d_k)
        k = self.fc_k(keys).view(b_s, nk, self.h, self.d_k).permute(0, 2, 3, 1)  # (b_s, h, d_k, nk)
        v = self.fc_v(values).view(b_s, nk, self.h, self.d_v).permute(0, 2, 1, 3)  # (b_s, h, nk, d_v)

        att = torch.matmul(q, k) / np.sqrt(self.d_k)  # (b_s, h, nq, nk)
        if attention_weights is not None:
            att = att * attention_weights
        if attention_mask is not None:
            att = att.masked_fill(attention_mask, -np.inf)
        att = torch.softmax(att, -1)
        att=self.dropout(att)

        out = torch.matmul(att, v).permute(0, 2, 1, 3).contiguous().view(b_s, nq, self.h * self.d_v)  # (b_s, nq, h*d_v)
        # print("OUT::",out.shape)
        out = self.fc_o(out)  # (b_s, nq, d_model)

        v2=v.permute(0,1,3,2).contiguous().view(b_s,-1,nk) #bs,dim,n
        self.dy_paras=nn.Parameter(self.softmax(self.dy_paras))
        out2=self.dy_paras[0]*self.conv1(v2)+self.dy_paras[1]*self.conv3(v2)+self.dy_paras[2]*self.conv5(v2)
        out2=out2.permute(0,2,1) #bs.n.dim

        out=out+out2
        out =  torch.unsqueeze(out,2)
        # print("OUT::",out.shape)

        return out        

'''
class MUSEAttention1(nn.Module):

    def __init__(self, d_model, d_k, d_v, h,dropout=.1):


        super(MUSEAttention1, self).__init__()
        d_model = d_model
        self.fc_q = nn.Linear(d_model, h * d_k)
        self.fc_k = nn.Linear(d_model, h * d_k)
        self.fc_v = nn.Linear(d_model, h * d_v)
        self.fc_o = nn.Linear(h * d_v, d_model)
        self.dropout = nn.Dropout(dropout)

        self.conv1=Depth_Pointwise_Conv1d(h * d_v, d_model,1)
        self.conv3=Depth_Pointwise_Conv1d(h * d_v, d_model,3)
        self.conv5=Depth_Pointwise_Conv1d(h * d_v, d_model,5)
        self.dy_paras=nn.Parameter(torch.ones(3))
        self.softmax=nn.Softmax(-1)

        self.d_model = d_model
        self.d_k =  d_k
        self.d_v =  d_v
        self.h = h

        self.init_weights()


    def init_weights(self):
        for m in self.modules():
            if isinstance(m, nn.Conv2d):
                init.kaiming_normal_(m.weight, mode='fan_out')
                if m.bias is not None:
                    init.constant_(m.bias, 0)
            elif isinstance(m, nn.BatchNorm2d):
                init.constant_(m.weight, 1)
                init.constant_(m.bias, 0)
            elif isinstance(m, nn.Linear):
                init.normal_(m.weight, std=0.001)
                if m.bias is not None:
                    init.constant_(m.bias, 0)

    def forward(self, queries, keys, values, attention_mask=None, attention_weights=None):

        #Self Attention
        # print("DEEEEEECODER::",queries.shape)
        b_s, nq = queries.shape[:2]
        # nq=3
        queries = queries.view(b_s, nq, -1)
        keys = keys.view(b_s, nq, -1)
        values = values.view(b_s, nq, -1)
        
        b_s, nq = queries.shape[:2]

        # print("SHAPE::::",self.fc_q(queries).shape) #[64, 7, 3, 128]
        # print("self.d_k SHAPE::::",self.d_k)
        # print("self.h SHAPE::::",self.h)
        # print("self.d_v SHAPE::::",self.d_v)
        nk = keys.shape[1]

        q = self.fc_q(queries).view(b_s, nq, self.h, self.d_k).permute(0, 2, 1, 3)  # (b_s, h, nq, d_k)
        k = self.fc_k(keys).view(b_s, nk, self.h, self.d_k).permute(0, 2, 3, 1)  # (b_s, h, d_k, nk)
        v = self.fc_v(values).view(b_s, nk, self.h, self.d_v).permute(0, 2, 1, 3)  # (b_s, h, nk, d_v)

        att = torch.matmul(q, k) / np.sqrt(self.d_k)  # (b_s, h, nq, nk)
        if attention_weights is not None:
            att = att * attention_weights
        if attention_mask is not None:
            att = att.masked_fill(attention_mask, -np.inf)
        att = torch.softmax(att, -1)
        att=self.dropout(att)

        out = torch.matmul(att, v).permute(0, 2, 1, 3).contiguous().view(b_s, nq, self.h * self.d_v)  # (b_s, nq, h*d_v)
        # print("OUT::",out.shape)
        out = self.fc_o(out)  # (b_s, nq, d_model)

        v2=v.permute(0,1,3,2).contiguous().view(b_s,-1,nk) #bs,dim,n
        self.dy_paras=nn.Parameter(self.softmax(self.dy_paras))
        out2=self.dy_paras[0]*self.conv1(v2)+self.dy_paras[1]*self.conv3(v2)+self.dy_paras[2]*self.conv5(v2)
        out2=out2.permute(0,2,1) #bs.n.dim

        out=out+out2
        out =  torch.unsqueeze(out,2)
        # print("OUT::",out.shape)

        return out  
'''

class MUSEAttention2(nn.Module):

    def __init__(self, d_model, d_k, d_v, h,dropout=.1):


        super(MUSEAttention2, self).__init__()
        # d_model = d_model*3
        self.fc_q = nn.Linear(d_model, h * d_k)
        d_model = d_model*3

        self.fc_k = nn.Linear(d_model, h * d_k)
        self.fc_v = nn.Linear(d_model, h * d_v)
        self.fc_o = nn.Linear(h * d_v, d_model//3)
        self.dropout = nn.Dropout(dropout)

        self.conv1=Depth_Pointwise_Conv1d(h * d_v, d_model//3,1)
        self.conv3=Depth_Pointwise_Conv1d(h * d_v, d_model//3,3)
        self.conv5=Depth_Pointwise_Conv1d(h * d_v, d_model//3,5)
        self.dy_paras=nn.Parameter(torch.ones(3))
        self.softmax=nn.Softmax(-1)

        self.d_model = d_model
        self.d_k =  d_k
        self.d_v =  d_v
        self.h = h

        self.init_weights()


    def init_weights(self):
        for m in self.modules():
            if isinstance(m, nn.Conv2d):
                init.kaiming_normal_(m.weight, mode='fan_out')
                if m.bias is not None:
                    init.constant_(m.bias, 0)
            elif isinstance(m, nn.BatchNorm2d):
                init.constant_(m.weight, 1)
                init.constant_(m.bias, 0)
            elif isinstance(m, nn.Linear):
                init.normal_(m.weight, std=0.001)
                if m.bias is not None:
                    init.constant_(m.bias, 0)

    def forward(self, queries, keys, values, attention_mask=None, attention_weights=None):

        #Self Attention
        # print("OUT::",out.shape)
        b_s, nq = queries.shape[:2]
        # nq=3
        queries = queries.view(b_s, nq, -1)
        keys = keys.view(b_s, nq, -1)
        values = values.view(b_s, nq, -1)
        
        b_s, nq = queries.shape[:2]

        # print("SHAPE::::",self.fc_q(queries).shape) #[64, 7, 3, 128]
        # print("self.d_k SHAPE::::",self.d_k)
        # print("self.h SHAPE::::",self.h)
        # print("self.d_v SHAPE::::",self.d_v)
        nk = keys.shape[1]

        q = self.fc_q(queries).view(b_s, nq, self.h, self.d_k).permute(0, 2, 1, 3)  # (b_s, h, nq, d_k)
        k = self.fc_k(keys).view(b_s, nk, self.h, self.d_k).permute(0, 2, 3, 1)  # (b_s, h, d_k, nk)
        v = self.fc_v(values).view(b_s, nk, self.h, self.d_v).permute(0, 2, 1, 3)  # (b_s, h, nk, d_v)

        att = torch.matmul(q, k) / np.sqrt(self.d_k)  # (b_s, h, nq, nk)
        if attention_weights is not None:
            att = att * attention_weights
        if attention_mask is not None:
            att = att.masked_fill(attention_mask, -np.inf)
        att = torch.softmax(att, -1)
        att=self.dropout(att)

        out = torch.matmul(att, v).permute(0, 2, 1, 3).contiguous().view(b_s, nq, self.h * self.d_v)  # (b_s, nq, h*d_v)
        # print("OUT::",out.shape)
        out = self.fc_o(out)  # (b_s, nq, d_model)

        v2=v.permute(0,1,3,2).contiguous().view(b_s,-1,nk) #bs,dim,n
        self.dy_paras=nn.Parameter(self.softmax(self.dy_paras))
        out2=self.dy_paras[0]*self.conv1(v2)+self.dy_paras[1]*self.conv3(v2)+self.dy_paras[2]*self.conv5(v2)
        out2=out2.permute(0,2,1) #bs.n.dim

        out=out+out2
        out =  torch.unsqueeze(out,2)
        # print("OUT::",out.shape)

        return out
        
        
        
        
        
        
        
        
        
'''      
class MUSEAttention2(nn.Module):

    def __init__(self, d_model, d_k, d_v, h,dropout=.1):


        super(MUSEAttention2, self).__init__()
        # d_model = d_model*3
        self.fc_q = nn.Linear(d_model, h * d_k)
        d_model = d_model*3

        self.fc_k = nn.Linear(d_model, h * d_k)
        self.fc_v = nn.Linear(d_model, h * d_v)
        self.fc_o = nn.Linear(h * d_v, d_model//3)
        self.dropout = nn.Dropout(dropout)

        self.conv1=Depth_Pointwise_Conv1d(h * d_v, d_model//3,1)
        self.conv3=Depth_Pointwise_Conv1d(h * d_v, d_model//3,3)
        self.conv5=Depth_Pointwise_Conv1d(h * d_v, d_model//3,5)
        self.dy_paras=nn.Parameter(torch.ones(3))
        self.softmax=nn.Softmax(-1)

        self.d_model = d_model
        self.d_k =  d_k
        self.d_v =  d_v
        self.h = h

        self.init_weights()


    def init_weights(self):
        for m in self.modules():
            if isinstance(m, nn.Conv2d):
                init.kaiming_normal_(m.weight, mode='fan_out')
                if m.bias is not None:
                    init.constant_(m.bias, 0)
            elif isinstance(m, nn.BatchNorm2d):
                init.constant_(m.weight, 1)
                init.constant_(m.bias, 0)
            elif isinstance(m, nn.Linear):
                init.normal_(m.weight, std=0.001)
                if m.bias is not None:
                    init.constant_(m.bias, 0)

    def forward(self, queries, keys, values, attention_mask=None, attention_weights=None):

        #Self Attention
        # print("OUT::",out.shape)
        b_s, nq = queries.shape[:2]
        # nq=3
        queries = queries.view(b_s, nq, -1)
        keys = keys.view(b_s, nq, -1)
        values = values.view(b_s, nq, -1)
        
        b_s, nq = queries.shape[:2]

        # print("SHAPE::::",self.fc_q(queries).shape) #[64, 7, 3, 128]
        # print("self.d_k SHAPE::::",self.d_k)
        # print("self.h SHAPE::::",self.h)
        # print("self.d_v SHAPE::::",self.d_v)
        nk = keys.shape[1]

        q = self.fc_q(queries).view(b_s, nq, self.h, self.d_k).permute(0, 2, 1, 3)  # (b_s, h, nq, d_k)
        k = self.fc_k(keys).view(b_s, nk, self.h, self.d_k).permute(0, 2, 3, 1)  # (b_s, h, d_k, nk)
        v = self.fc_v(values).view(b_s, nk, self.h, self.d_v).permute(0, 2, 1, 3)  # (b_s, h, nk, d_v)

        att = torch.matmul(q, k) / np.sqrt(self.d_k)  # (b_s, h, nq, nk)
        if attention_weights is not None:
            att = att * attention_weights
        if attention_mask is not None:
            att = att.masked_fill(attention_mask, -np.inf)
        att = torch.softmax(att, -1)
        att=self.dropout(att)

        out = torch.matmul(att, v).permute(0, 2, 1, 3).contiguous().view(b_s, nq, self.h * self.d_v)  # (b_s, nq, h*d_v)
        # print("OUT::",out.shape)
        out = self.fc_o(out)  # (b_s, nq, d_model)

        v2=v.permute(0,1,3,2).contiguous().view(b_s,-1,nk) #bs,dim,n
        self.dy_paras=nn.Parameter(self.softmax(self.dy_paras))
        out2=self.dy_paras[0]*self.conv1(v2)+self.dy_paras[1]*self.conv3(v2)+self.dy_paras[2]*self.conv5(v2)
        out2=out2.permute(0,2,1) #bs.n.dim

        out=out+out2
        out =  torch.unsqueeze(out,2)
        # print("OUT::",out.shape)

        return out
'''        
def make_model(src_vocab, tgt_vocab, N=6, d_model=32, d_ff=64, h=8, dropout=0.1,spatial=False):
    "Helper: Construct a model from hyperparameters."
    c = copy.deepcopy
    #attn = MultiHeadedAttention(h, d_model)
    attn = MUSEAttention(d_model=d_model, d_k=d_model, d_v=d_model, h=h)
    attn1 = MUSEAttention1(d_model=d_model, d_k=d_model, d_v=d_model, h=h)
    attn2 = MUSEAttention2(d_model=d_model, d_k=d_model, d_v=d_model, h=h)

    ff = PositionwiseFeedForward(d_model, d_ff, dropout)
    position = PositionalEncoding(d_model, dropout)
    if(spatial):
        model = EncoderDecoder(
        Encoder(EncoderLayer(d_model, c(attn), c(ff), dropout), N),
        Decoder(DecoderLayer(d_model, c(attn1), c(attn2), 
                            c(ff), dropout), N),
                            Embeddings(src_vocab, d_model),
                            Embeddings(tgt_vocab, d_model),
                            Generator(d_model, tgt_vocab))
    else:
        model = EncoderDecoder(
            Encoder(EncoderLayer(d_model, c(attn), c(ff), dropout), N),
            Decoder(DecoderLayer(d_model, c(attn1), c(attn2), 
                                c(ff), dropout), N),
            nn.Sequential(Embeddings(src_vocab, d_model), c(position)),
            nn.Sequential(Embeddings(tgt_vocab, d_model), c(position)),
            Generator(d_model, tgt_vocab))

    # This was important from their code. 
    # Initialize parameters with Glorot / fan_avg.
    for p in model.parameters():
        if p.dim() > 1:
            nn.init.xavier_uniform(p)
    return model 
    