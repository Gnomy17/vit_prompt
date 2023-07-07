import numpy as np
import tqdm
import random
from functools import partial
import torch.nn as nn
import torch.nn.functional as F
from timm.loss import  SoftTargetCrossEntropy
from timm.data import Mixup
from parser_cifar import get_args
from auto_LiRPA.utils import MultiAverageMeter
from utils import *
from torch.autograd import Variable
from pgd import evaluate_pgd,evaluate_CW
from evaluate import evaluate_aa
from auto_LiRPA.utils import logger
import matplotlib.pyplot as plt
# torch.autograd.set_detect_anomaly(True)
args = get_args()
joint_p = lambda x, y: torch.cat((x, y), dim=1) if y is not None else x 
def make_prompt(length, h_dim, init_xavier=True):
    prompt = torch.zeros(1, length, h_dim, requires_grad=True)
    prompt.cuda()
    if init_xavier:
        nn.init.xavier_uniform_(prompt)
    # prompt = nn.Parameter(prompt)
    return prompt

args.out_dir = args.out_dir+"_"+args.dataset+"_"+args.model+"_"+args.method+"_warmup"
args.out_dir = args.out_dir +"/seed"+str(args.seed)
if args.ARD:
    args.out_dir = args.out_dir + "_ARD"
if args.PRM:
    args.out_dir = args.out_dir + "_PRM"
if args.scratch:
    args.out_dir = args.out_dir + "_no_pretrained"
# if args.load:
#     args.out_dir = args.out_dir + "_load"


args.out_dir = args.out_dir + "/weight_decay_{:.6f}/".format(
        args.weight_decay)+ "drop_rate_{:.6f}/".format(args.drop_rate)+"nw_{:.6f}/".format(args.n_w)


print(args.out_dir)
os.makedirs(args.out_dir,exist_ok=True)
logfile = os.path.join(args.out_dir, 'log_{:.4f}.log'.format(args.weight_decay))

file_handler = logging.FileHandler(logfile)
file_handler.setFormatter(logging.Formatter('%(levelname)-8s %(asctime)-12s %(message)s'))
logger.addHandler(file_handler)

logger.info(args)

np.random.seed(args.seed)
torch.manual_seed(args.seed)
torch.cuda.manual_seed(args.seed)

resize_size = args.resize
crop_size = args.crop
mean1 = 0
mean2 = 0
mean3 = 0
count = 0

train_loader, test_loader= get_loaders(args)
if args.model == "vit_base_patch16_224":
    from model_for_cifar.vit import vit_base_patch16_224
    model = vit_base_patch16_224(pretrained = (not args.scratch),img_size=crop_size,num_classes =10,patch_size=args.patch, args=args).cuda()
    model = nn.DataParallel(model)
elif args.model == "vit_small_robust_cifar":
    from model_for_cifar.vit import vit_small_patch16_224
    model = vit_small_patch16_224(pretrained = (not args.scratch),img_size=crop_size,num_classes =10,patch_size=args.patch, args=args).cuda()
    model = nn.DataParallel(model)
    chkpnt = torch.load(r'./finetuned_model/robust_cifar_vit')
    model.load_state_dict(chkpnt['state_dict'])
    chkpnt['state_dict'] = 0
elif args.model == 'vit_finetuned_cifar':
    #### TODO ####
    chkpnt = torch.load(r'./finetuned_model/finetuned_vit')
    from model_for_cifar.vit import vit_base_patch16_224
    model = vit_base_patch16_224(pretrained = (not args.scratch),img_size=crop_size,num_classes =10,patch_size=args.patch, args=args).cuda()
    model = nn.DataParallel(model)
    model.load_state_dict(chkpnt['state_dict'])
elif args.model == "vit_base_patch16_224_in21k":
    from model_for_cifar.vit import vit_base_patch16_224_in21k
    model = vit_base_patch16_224_in21k(pretrained = (not args.scratch),img_size=crop_size,num_classes =10,patch_size=args.patch, args=args).cuda()
    model = nn.DataParallel(model)
elif args.model == "vit_small_patch16_224":
    from model_for_cifar.vit import  vit_small_patch16_224
    model = vit_small_patch16_224(pretrained = (not args.scratch),img_size=crop_size,num_classes =10,patch_size=args.patch, args=args).cuda()
    model = nn.DataParallel(model)
elif args.model == "deit_small_patch16_224":
    from model_for_cifar.deit import  deit_small_patch16_224
    model = deit_small_patch16_224(pretrained = (not args.scratch),img_size=crop_size,num_classes =10, patch_size=args.patch, args=args).cuda()
    model = nn.DataParallel(model)
elif args.model == "deit_tiny_patch16_224":
    from model_for_cifar.deit import  deit_tiny_patch16_224
    model = deit_tiny_patch16_224(pretrained = (not args.scratch),img_size=crop_size,num_classes =10,patch_size=args.patch, args=args).cuda()
    model = nn.DataParallel(model)
elif args.model == "convit_base":
    from model_for_cifar.convit import convit_base
    model = convit_base(pretrained = (not args.scratch),img_size=crop_size,num_classes =10,patch_size=args.patch, args=args).cuda()
    model = nn.DataParallel(model)
elif args.model == "convit_small":
    from model_for_cifar.convit import convit_small
    model = convit_small(pretrained = (not args.scratch),img_size=crop_size,num_classes =10,patch_size=args.patch,args=args).cuda()
    model = nn.DataParallel(model)
elif args.model == "convit_tiny":
    from model_for_cifar.convit import convit_tiny
    model = convit_tiny(pretrained = (not args.scratch),img_size=crop_size,num_classes =10,patch_size=args.patch, args=args).cuda()
    model = nn.DataParallel(model)
else:
    raise ValueError("Model doesn't exist！")
if args.model_log:
    logger.info('Model{}'.format(model))
model.train()

checkpoint = None
if args.load:
    checkpoint = torch.load(args.load_path)
    model.load_state_dict(checkpoint['state_dict'])


if args.prompted or args.prompt_too or args.method in ['PAT_tar', 'splits']:
    # from model_for_cifar.prompt import Prompt
    # prompt = Prompt(args.prompt_length, 768)
    
    # if args.load:
    #     prompt.load_state_dict(checkpoint['prompt'])
    # params = []
    # prompt.train()
    # prompt.cuda()
    if args.method == 'splits':
        done_prompt = None
    if args.load:
        if args.method == 'splits':
            if args.just_eval:
                prompt = joint_p(checkpoint['prompt'][0], checkpoint['done'][0])
            else:
                prompt = checkpoint['prompt'][0]
                done_prompt = checkpoint['done'][0]
        else:
            prompt = (checkpoint['prompt'])[0]
    else:
        prompt = make_prompt(args.prompt_length, 768)
    prompts = [prompt]
    params = [prompt]
    # params = {'prompt': prompt}
    # prev_p = torch.zeros_like(prompt)
    # prev_p.requires_grad = False
    if args.disjoint_prompts:
        prompt2 = make_prompt(args.prompt_length, 768)
        params.append(prompt2)
    if args.method == 'PAT_tar':
        dim = 768
        pred_dim = 512
        predictor = nn.Sequential(nn.Linear(dim, pred_dim, bias=False),
                                    nn.BatchNorm1d(pred_dim),
                                    nn.ReLU(inplace=True), # hidden layer
                                    nn.Linear(pred_dim, dim))
        predictor.cuda()
        params += list(predictor.parameters())
    if args.prompt_too:
        for p in model.parameters():
            params.append(p)
    else:
        for p in model.module.head.parameters():
            params.append(p)
        
    if args.optim == 'sgd':
        opt = torch.optim.SGD(params, lr=args.lr_max, momentum=args.momentum, weight_decay=args.weight_decay) 
    elif args.optim == 'adam':
        opt = torch.optim.Adam(params, lr=args.lr_max, weight_decay=args.weight_decay)
elif args.method in ['voting']:
    prompts = []
    head_params = []
    opts = []
    for p in model.module.head.parameters():
        head_params.append(p)
    for i in range(args.num_prompts):
        if args.load:
            p = (checkpoint['prompts'])[i]
        else:
            p = make_prompt(args.prompt_length, 768)
        prompts.append(p)
        
    # print(len(prompts))
    # print(args.num_prompts)
        o  = torch.optim.SGD([p] + head_params, lr=args.lr_max, momentum=args.momentum, weight_decay=args.weight_decay)
        opts.append(o)
elif args.blocked:
    from model_for_cifar.vit import PatchEmbed, Block
    from model_for_cifar.prompt import PromptBlock
    print(args.block_type)
    if args.block_type == "attention":
        prompt = nn.Sequential(PatchEmbed(img_size=crop_size, patch_size=args.patch, in_chans=3, embed_dim=768), Block(768, 12))
    elif args.block_type == "cnn":
        prompt = PromptBlock(img_size=crop_size, patch_size= args.patch, in_chans=3, middle_dim=768 ,embed_dim= 768, stride=4)
    prompt.cuda()
    prompt.train()
    params = []
    for p in prompt.parameters():
        params.append(p)
    for p in model.module.head.parameters():
        params.append(p) 
    opt = torch.optim.SGD(params, lr=args.lr_max, momentum=args.momentum, weight_decay=args.weight_decay)
else:
    if args.optim == 'sgd':
        opt = torch.optim.SGD(model.parameters(), lr=args.lr_max, momentum=args.momentum, weight_decay=args.weight_decay)
    elif args.optim == 'adam':
        opt = torch.optim.Adam(model.parameters(), lr = args.lr_max, weight_decay=args.weight_decay)

def evaluate_natural(args, model, test_loader, verbose=False, prompt=None):
    model.eval()
    with torch.no_grad():
        meter = MultiAverageMeter()
        test_loss = test_acc = test_n = 0
        def test_step(step, X_batch, y_batch):
            X, y = X_batch.cuda(), y_batch.cuda()
            if prompt is not None:
                output = model(X, prompt)
            else:
                output = model(X)
            loss = F.cross_entropy(output, y)
            meter.update('test_loss', loss.item(), y.size(0))
            meter.update('test_acc', (output.max(1)[1] == y).float().mean(), y.size(0))
        for step, (X_batch, y_batch) in enumerate(test_loader):
            test_step(step, X_batch, y_batch)
        logger.info('Evaluation {}'.format(meter))


mu = torch.tensor(cifar10_mean).view(3, 1, 1).cuda()
std = torch.tensor(cifar10_std).view(3, 1, 1).cuda()

upper_limit = ((1 - mu) / std).cuda()
lower_limit = ((0 - mu) / std).cuda()

def simul_pgd(model, X, y, epsilon_base, alpha, args, criterion, handle_list, drop_rate,iters=None, prompts=None, target=None):
    model.eval()
    epsilon = epsilon_base.cuda()
    delta = torch.zeros_like(X).cuda()
    if args.delta_init == 'random':
        for i in range(len(epsilon)):
            delta[:, i, :, :].uniform_(-epsilon[i][0][0].item(), epsilon[i][0][0].item())
        delta.data = clamp(delta, lower_limit - X, upper_limit - X)
    delta.requires_grad = True
    for _ in range(args.attack_iters if iters is None else iters):
        # patch drop
        add_noise_mask = torch.ones_like(X)
        grid_num_axis = int(args.resize / args.patch)
        max_num_patch = grid_num_axis * grid_num_axis
        ids = [i for i in range(max_num_patch)]
        random.shuffle(ids)
        num_patch = int(max_num_patch * (1 - drop_rate))
        if num_patch !=0:
            ids = np.array(ids[:num_patch])
            rows, cols = ids // grid_num_axis, ids % grid_num_axis
            for r, c in zip(rows, cols):
                add_noise_mask[:, :, r * args.patch:(r + 1) * args.patch,
                c * args.patch:(c + 1) * args.patch] = 0
        if args.PRM:
            delta = delta * add_noise_mask
        loss = None
        for p in prompts:
            out = model(X + delta, p)
            if loss is None:
                if target is None:
                    loss = criterion(out, y)
                else:
                    loss = -criterion(out, target)
            else:
                if target is None:
                    loss += criterion(out, y)
                else:
                    loss -= criterion(out, target)
        grad = torch.autograd.grad(loss, delta)[0].detach()
        delta.data = clamp(delta + alpha * torch.sign(grad), -epsilon, epsilon)
        delta.data = clamp(delta, lower_limit - X, upper_limit - X)
    delta = delta.detach()
    model.train()
    if len(handle_list) != 0:
        for handle in handle_list:
            handle.remove()
    return delta

def pgd_attack(model, X, y, epsilon_base, alpha, args, criterion, handle_list, drop_rate,iters=None, prompt=None, target=None, avoid= None, deep=False):
    model.eval()
    epsilon = epsilon_base.cuda()
    delta = torch.zeros_like(X).cuda()
    if args.delta_init == 'random':
        for i in range(len(epsilon)):
            delta[:, i, :, :].uniform_(-epsilon[i][0][0].item(), epsilon[i][0][0].item())
        delta.data = clamp(delta, lower_limit - X, upper_limit - X)
    delta.requires_grad = True
    for _ in range(args.attack_iters if iters is None else iters):
        # patch drop
        add_noise_mask = torch.ones_like(X)
        grid_num_axis = int(args.resize / args.patch)
        max_num_patch = grid_num_axis * grid_num_axis
        ids = [i for i in range(max_num_patch)]
        random.shuffle(ids)
        num_patch = int(max_num_patch * (1 - drop_rate))
        if num_patch !=0:
            ids = np.array(ids[:num_patch])
            rows, cols = ids // grid_num_axis, ids % grid_num_axis
            for r, c in zip(rows, cols):
                add_noise_mask[:, :, r * args.patch:(r + 1) * args.patch,
                c * args.patch:(c + 1) * args.patch] = 0
        if args.PRM:
            delta = delta * add_noise_mask
        if prompt is not None:
            if callable(prompt):
                output = model(X + delta, prompt(X + delta), deep=deep)
            else:
                output = model(X + delta, prompt, deep=deep)
        else:    
            output = model(X + delta)
        if target is None:
            if avoid is None:
                loss = criterion(output, y)
            elif y is None:
                loss = criterion(output, avoid)
            else:
                loss = criterion(output, y) + criterion(output, avoid)
        else:
            loss = -criterion(output, target)
        grad = torch.autograd.grad(loss, delta)[0].detach()
        delta.data = clamp(delta + alpha * torch.sign(grad), -epsilon, epsilon)
        delta.data = clamp(delta, lower_limit - X, upper_limit - X)
    delta = delta.detach()
    model.train()
    if len(handle_list) != 0:
        for handle in handle_list:
            handle.remove()
    return delta



def CW_loss(out, y_oh, t=0):
    loss = 0
    y = y_oh.max(1)[1]
    for j in range(out.size(1)):
        inds = torch.Tensor([k for k in range(out.size(1)) if k != j]).long()
        outs_c = out[y == j]
        loss += torch.max(torch.max(outs_c[:, inds]) - outs_c[:, j], t)[0].sum()
    loss /= out.size(0)
    return loss

def majority_vote(model, X, y, prompts):
    with torch.no_grad():
        outs = []
        votes = torch.zeros_like(y)
        for p in prompts:
            out = model(X, p)
            outs.append(out.max(1)[1].detach())
            votes += F.one_hot(out.max(1)[1], 10)
        # print(votes)
        return (votes.max(1)[1] == y.max(1)[1]).float().mean().item(), outs


def train_adv(args, model, ds_train, ds_test, logger):
    global prompt, done_prompt, opt

    epsilon_base = (args.epsilon / 255.) / std
    alpha = (args.alpha / 255.) / std

    train_loader, test_loader = ds_train, ds_test

    mixup_fn = None
    mixup_active = args.mixup > 0 or args.cutmix > 0. or args.cutmix_minmax is not None
    if mixup_active :
        mixup_fn = Mixup(
            mixup_alpha=args.mixup, cutmix_alpha=args.cutmix, cutmix_minmax=args.cutmix_minmax,
            prob=args.mixup_prob, switch_prob=args.mixup_switch_prob, mode=args.mixup_mode,
            label_smoothing=args.labelsmoothvalue, num_classes=10)

    # if mixup_active:
    #     criterion = SoftTargetCrossEntropy()
    # else:
    criterion = nn.CrossEntropyLoss()

    steps_per_epoch = len(train_loader)
    
    
    if args.load:
        # if args.method == 'voting':
        #     for i, o in enumerate(opts):
        #         o.load_state_dict(checkpoint['opts'][i])
        # else:
        # opt.load_state_dict(checkpoint['opts'])
        logger.info("Resuming at epoch {}".format(checkpoint['epoch'] + 1))
        # del checkpoint
    if args.delta_init == 'previous':
        delta = torch.zeros(args.batch_size, 3, 32, 32).cuda()
    lr_steps = args.epochs * steps_per_epoch
    def lr_schedule(t):
        if t< args.epochs-5:
            return args.lr_max
        elif t< args.epochs-2:
            return args.lr_max*0.1
        else:
            return args.lr_max * 0.01
    epoch_s = 0 if not args.load else (checkpoint['epoch'])
    if args.load:
        for k in checkpoint:
            checkpoint[k] = None
    # prev_prompt = Prompt(args.prompt_length, 768)
    # prev_prompt.set_prompt(prompt)
    # last_ind = args.prompt_length
    # current_ind = int(args.prompt_length* (4/5))
    for epoch in tqdm.tqdm(range(epoch_s + 1, args.epochs + 1)):
        if args.just_eval:
            break
        # evaluate_natural(args, model, test_loader, verbose=False)
        train_loss = 0
        train_acc = 0
        train_clean = 0
        train_prompted = 0
        train_n = 0
        hist_c = torch.zeros((10)).cuda()
        hist_a = torch.zeros((10)).cuda()
        corr_mats = [torch.zeros((1 + 2,10,10)) for _ in (range(1))]
        # p10 = prompt.detach().clone()
        # p10.requires_grad = False

        def train_step(X, y, t, mixup_fn, hist_a, hist_c, corr_mats):
            global prompt, done_prompt, opt
            model.train()
            # drop_calculation
            def attn_drop_mask_grad(module, grad_in, grad_out, drop_rate):
                new = np.random.rand()
                if new > drop_rate:
                    gamma = 0
                else:
                    gamma = 1
                if len(grad_in) == 1:
                    mask = torch.ones_like(grad_in[0]) * gamma
                    return (mask * grad_in[0][:],)
                else:
                    mask = torch.ones_like(grad_in[0]) * gamma
                    mask_1 = torch.ones_like(grad_in[1]) * gamma
                    return (mask * grad_in[0][:], mask_1 * grad_in[1][:])
            if t < args.n_w:
                drop_rate = t / args.n_w * args.drop_rate
            else:
                drop_rate = args.drop_rate
            drop_hook_func = partial(attn_drop_mask_grad, drop_rate=drop_rate)
            model.eval()
            handle_list = list()
            if args.model in ["vit_base_patch16_224", "vit_base_patch16_224_in21k", "vit_small_patch16_224"]:
                if args.ARD:
                    from model_for_cifar.vit import Block
                    for name, module in model.named_modules():
                        if isinstance(module, Block):
                            handle_list.append(module.drop_path.register_backward_hook(drop_hook_func))
            elif args.model in ["deit_small_patch16_224", "deit_tiny_patch16_224"]:
                if args.ARD:
                    from model_for_cifar.deit import Block
                    for name, module in model.named_modules():
                        if isinstance(module, Block):
                            handle_list.append(module.drop_path.register_backward_hook(drop_hook_func))
            elif args.model in ["convit_base", "convit_small", "convit_tiny"]:
                if args.ARD:
                    from model_for_cifar.convit import Block
                    for name, module in model.named_modules():
                        if isinstance(module, Block):
                            handle_list.append(module.drop_path.register_backward_hook(drop_hook_func))
            model.train()
            if args.method == 'AT' or (args.method == 'ws' and  args.ws <= epoch):
                X = X.cuda()
                y = y.cuda()
                if mixup_fn is not None:
                    X, y = mixup_fn(X, y)
                
                if args.prompted or args.prompt_too:
                    if args.full_white:
                        delta = pgd_attack(model, X, y, epsilon_base, alpha, args, criterion, handle_list, drop_rate, prompt=prompt).detach()
                        td = pgd_attack(model, X, None, epsilon_base, alpha, args, criterion, handle_list, drop_rate, prompt=prompt, target=y).detach()
                        # prev_prompt.set_prompt(prompt)
                    elif args.all_classes:
                        i = torch.randint(low=1, high=y.size(1), size=y.max(1)[1].size()).cuda()
                        tars = F.one_hot((y.max(1)[1] + i) % y.size(1), y.size(1))
                        # print(tars.size())
                        delta = pgd_attack(model, X, y, epsilon_base, alpha, args, criterion, handle_list, drop_rate, prompt=prompt, target=tars).detach()
                        output = model(X + delta, prompt)
                        loss = criterion(output, y)
                    else:
                        delta = pgd_attack(model, X, y, epsilon_base, alpha, args, criterion, handle_list, drop_rate, prompt=prompt2 if args.disjoint_prompts else None).detach()
                    X.detach()
                    outa = model(X + delta, prompt)
                    outt = model(X + td, prompt)
                    output = (outa + outt).detach()/2
                    loss = criterion(outa, y) + criterion(outt, y)
                    # if not args.all_classes:
                    #     X_adv = X + delta
                    #     if args.disjoint_prompts:
                    #         output = model(X_adv, prompt)
                    #     else: 
                    #         output = model(X_adv, prompt)
                    #     loss = CW_loss(output, y) if args.cw else criterion(output, y)
                    #     if args.disjoint_prompts:
                    #         loss += F.mse_loss(output, model(X+delta, prompt2))
                    # if args.mix_lam > 0:
                    #     out = model(X, prompt)
                    #     loss += (args.mix_lam * criterion(out, y))
                    #     loss /= (1 + args.mix_lam)
                elif args.blocked:
                    delta = pgd_attack(model, X, y, epsilon_base, alpha, args, criterion, handle_list, drop_rate, prompt=prompt).detach()
                    X_adv = X + delta
                    output = model(X_adv, prompt(X_adv))
                    loss = criterion(output, y)
                    if args.mix_lam > 0:
                        out = model(X, prompt(X))
                        loss += (args.mix_lam * criterion(out, y))
                        loss /= (1 + args.mix_lam)
                else:
                    delta = pgd_attack(model, X, y, epsilon_base, alpha, args, criterion, handle_list, drop_rate).detach()
                    X_adv = X + delta
                    output = model(X_adv)
                    loss = criterion(output, y)
                # output = model(X, prompt)
            elif args.method == 'voting':
                X = X.cuda()
                y = y.cuda()
                if mixup_fn is not None:
                    X, y = mixup_fn(X, y)
                ds = []
                tds = []
                # tar = F.one_hot((y.max(1)[1])%10, 10).float().cuda()
                for i, p in enumerate(prompts):
                
                    d = pgd_attack(model, X, y, epsilon_base, alpha, args, criterion, handle_list, drop_rate, prompt=p).detach()
                    # td = pgd_attack(model, X, y, epsilon_base, alpha, args, criterion, handle_list, drop_rate, prompt=p, target=y).detach()
                    # tds.append(td)
                    ds.append(d)
                accs = torch.zeros(len(prompts))
                losses = 0
                for i, p in enumerate(prompts):
                    loss = 0
                    acc = 0
                    out_c = model(X, p)
                    hist_c += F.one_hot(out_c.max(1)[1], 10).sum(dim=0)
                    inds = y.max(1)[1]
                    inds_c = out_c.max(1)[1]
                    for j in range(y.size(0)):
                        corr_mats[i][0, inds[j], inds_c[j]] += 1

                    if args.voting_method == 'all':
                        ind = i + 1 if i%2 == 0 else i - 1
                        next_d = ds[ind]
                        out = model(X + next_d, p)
                        
                        acc += (out.max(1)[1] == y.max(1)[1]).float().mean().item()
                        loss += criterion(out, y) #+ F.mse_loss()
                        for j in range(y.size(0)):
                            corr_mats[i][ind + 1, inds[j], out.max(1)[1][j]] += 1

                        with torch.no_grad():
                            for k, d in enumerate(ds):
                                if k == ind:
                                    continue
                                oute = model(X + d, p).detach()
                                for j in range(y.size(0)):
                                    corr_mats[i][k + 1, inds[j], oute.max(1)[1][j]] += 1
                        
                    elif args.voting_method == 'self':
                        out = model(X + ds[i], p)
                        inds_u = out.max(1)[1]
                        for j in range(y.size(0)):
                            corr_mats[i][i + 1, inds[j], inds_u[j]] += 1
                        hist_a += F.one_hot(out.max(1)[1], 10).sum(dim=0)
                        acc += (y.max(1)[1] == out.max(1)[1]).float().mean().item()
                        
                        # unilabs = torch.ones_like(y)/10
                        # unilabs = F.one_hot((y.max(1)[1] + 1) % 10).float()
                        loss += criterion(out, y) + criterion(out_c, y)
                        with torch.no_grad():
                            for k, d in enumerate(ds):
                                if k == (i):
                                    continue
                                outu = model(X + d, p).detach()
                                for j in range(y.size(0)):
                                    corr_mats[i][k + 1, inds[j], outu.max(1)[1][j]] += 1
                    elif args.voting_method == 'rand':
                        # ds = torch.stack(ds)
                        # inds = torch.randint(low=0, high=len(prompts), size=(ds.size(0),))
                        # rand_d = ds[:, :, :, :, inds]
                        # cosim = nn.CosineSimilarity(2)
                        ind = (i+(1))%len(ds)
                        # ind = (i + torch.randint(low=1, high=len(ds), size=1).item()) % len(ds)
                        # print(ind)
                        next_d = ds[ind]
                        out = model(X + next_d, p)
                        
                        acc += (out.max(1)[1] == y.max(1)[1]).float().mean().item()
                        loss += criterion(out, y) #+ F.mse_loss()

                        outu = model(X + tds[ind], p).detach()
                        for j in range(y.size(0)):
                            corr_mats[i][i + 1, inds[j], outu.max(1)[1][j]] += 1
                        # indu = (ind + 1) % 2
                        # for j in range(y.size(0)):
                        #     corr_mats[i][indu + 1, inds[j],out.max(1)[1][j]] += 1
                        with torch.no_grad():
                            for k, d in enumerate(ds):
                                if k == i:
                                    continue
                                oute = model(X + d, p).detach()
                                for j in range(y.size(0)):
                                    corr_mats[i][k + 1, inds[j], oute.max(1)[1][j]] += 1
                    
                    # loss += criterion(out_c, y)
                    
                    accs[i] = acc                
                    opts[i].zero_grad()
                    model.zero_grad()
                    loss.backward()
                    opts[i].step()
                    opts[i].zero_grad()
                    model.zero_grad()
                    losses += loss.item()

                accs_vote = torch.zeros(len(prompts))
                losses /= len(prompts)
                
                
                #acc_clean, outs = majority_vote(model, X, y, prompts)

                delta = 0#simul_pgd(model, X, y, epsilon_base, alpha, args, criterion, handle_list, drop_rate, prompts=prompts).detach()
                #acc_adv, outs = majority_vote(model, X + delta, y, prompts)
                # for i, p in enumerate(prompts):
                    # outc = model(X, p).detach()
                    # labs = F.one_hot(outc.max(1)[1], 10).float()
                    # d = pgd_attack(model, X, labs, epsilon_base, alpha, args, criterion, handle_list, drop_rate, prompt=p).detach()
                    # outu = model(X + d, p).detach()
                    
                    # majority_vote(model, X + d, y, prompts)
                # for k, o in enumerate(outs):
                #     # print(o.size())
                #     accs_vote[k] = (o == y.max(1)[1]).float().mean().item()
                #     for j in range(y.size(0)):
                #         corr_mats[k][len(prompts) + 1, inds[j], o[j]] += 1
                return accs, accs_vote, losses, 0, 0

            elif args.method == 'natural' or (args.method == 'ws' and epoch < args.ws):
                X = X.cuda()
                y = y.cuda()
                if mixup_fn is not None:
                    X, y = mixup_fn(X, y)
                if args.prompted or args.prompt_too:
                    output = model(X, prompt)
                elif args.blocked:
                    output = model(X, prompt(X))
                else:
                    output = model(X)
                # print(output.shape, y.shape)
                loss = criterion(output, y)
            elif args.method == 'PAT_tar':
                X = X.cuda()
                y = y.cuda()
                if mixup_fn is not None:
                    X, y = mixup_fn(X, y)
                # ds = []
                acc = 0
                loss = 0
                losses = 0
                
                outc, fc = model(X, prompt, get_fs=True)
                d = pgd_attack(model, X, y, epsilon_base, alpha, args, criterion, handle_list, drop_rate, prompt=prompt).detach()
                # d10 = pgd_attack(model, X, y, epsilon_base, alpha, args, criterion, handle_list, drop_rate, prompt=p10).detach()
                # d20 =  pgd_attack(model, X, y, epsilon_base, alpha, args, criterion, handle_list, drop_rate, prompt=p20).detach()
                # outa.detach()
                # fc.detach()
                # fa.detach()
                
                # if (step + 1) % 10 == 0:
                    # outc = model(X, prompt)
                    # loss += criterion(outc, y)
                    # pc20 = pc10.detach().clone()
                    # p10.data = prompt.detach().data
                    # pc20.requires_grad = False
                    # p10.requires_grad = False
                    # outa = outa.detach()
                    # outa = model(X + d, prompt).detach()
                    # fa.detach()
                    # outa= outc
                # else:
                outa, fa = model(X + d, prompt, get_fs=True)
                outa = outa.detach()
                loss = criterion(outc, y)
                # outu = model(X + d10, prompt).detach()
                outc.detach()
                closs =  (F.mse_loss(predictor(fa[:, args.prompt_length, :]), fc[:, args.prompt_length, :].detach()) + F.mse_loss(fa[:, args.prompt_length, :].detach(), predictor(fc[:, args.prompt_length, :])))
                count += 1
                mean1 += loss.item()
                mean2 += closs.item()
                if (step + 1) % 10 == 0:
                        logger.info("{:.4f}, {:.4f}".format(mean1/count, mean2/count))
                loss += closs
                # cosim = nn.CosineSimilarity(2)
                # loss -= cosim(fc[:, :, :], fa[:, :, :]).mean()
                # losses += loss.item()
                # opt.zero_grad()
                # model.zero_grad()
                # loss.backward()
                # opt.step()
                # opt.zero_grad()
                # model.zero_grad()
                # i = torch.randint(0, 10, (1,1)).item()
                # tar = torch.ones(y.size(0)).long().cuda() * i
                # tar = F.one_hot(tar, y.size(1)).float()
                
                # out = model(X + d, prompt)
                acc = (outc.max(1)[1] == y.max(1)[1]).float().mean().item()
                # loss += criterion(out, y)
                # losses += loss.item()
                opt.zero_grad()
                model.zero_grad()
                loss.backward()
                # opt.step()
                # opt.zero_grad()
                # model.zero_grad()
                # delta = pgd_attack(model, X, y, epsilon_base, alpha, args, criterion, handle_list, drop_rate, prompt=prompt).detach()
                # outa = model(X + delta, prompt).detach() 
                for j in range(y.size(0)):
                    corr_mats[0][0, y.max(1)[1][j], outc.detach().max(1)[1][j]] += 1
                    corr_mats[0][1, y.max(1)[1][j], outa.detach().max(1)[1][j]] += 1
                    corr_mats[0][2, y.max(1)[1][j], outa.detach().max(1)[1][j]] += 1
                # outu = model(X + delta, prompt).detach()
                acc_a = (outa.max(1)[1] == y.max(1)[1]).float().mean().item() 
                return loss, acc, y, acc_a, handle_list
            elif args.method == 'splits':
                
                X = X.cuda()
                y = y.cuda()
                if mixup_fn is not None:
                    X, y = mixup_fn(X, y)
                # yc = y.detach().clone()
                # X = X[y.max(1)[1] != 9]
                # y = y[y.max(1)[1] != 9]
                outc = model(X, joint_p(prompt, done_prompt))
                delta = pgd_attack(model, X, y, epsilon_base, alpha, args, criterion, handle_list, drop_rate, prompt=done_prompt).detach()
                losses= 0
                # print(prompt[:,current_ind:last_ind,:].size())
                
                
                deltaa = pgd_attack(model, X, y, epsilon_base, alpha, args, criterion, handle_list, drop_rate, prompt=joint_p(prompt, done_prompt)).detach()
                outa = model(X + deltaa, joint_p(prompt, done_prompt)).detach()
                acc_a = (outa.max(1)[1] == y.max(1)[1]).float().mean().item() 

                out = model(X + delta, joint_p(prompt, done_prompt))
                loss = criterion(out, y) + criterion(outc, y)
                # if done_prompt is not None:
                #     print(torch.autograd.grad(loss, done_prompt))
                loss.backward()
                # if last_ind < args.prompt_length:
                    # prompt.grad[:,last_ind:,:] *= 0
                losses += loss.item()
                acc = (out.max(1)[1] == y.max(1)[1]).float().mean().item()
                for j in range(y.size(0)):
                    corr_mats[0][0, y.max(1)[1][j], outc.detach().max(1)[1][j]] += 1
                    corr_mats[0][1, y.max(1)[1][j], out.detach().max(1)[1][j]] += 1
                    corr_mats[0][2, y.max(1)[1][j], outa.detach().max(1)[1][j]] += 1
                return loss, acc, y, acc_a, handle_list
            elif args.method == 'TRADES':
                X = X.cuda()
                y = y.cuda()
                epsilon = epsilon_base.cuda()
                beta = args.beta
                batch_size = len(X)
                if args.delta_init == 'random':
                    delta = 0.001 * torch.randn(X.shape).cuda()
                    delta.data = clamp(delta, lower_limit - X, upper_limit - X)
                criterion_kl = nn.KLDivLoss(size_average=False)
                model.eval()

                delta.requires_grad = True
                for _ in range(args.attack_iters):
                    add_noise_mask = torch.ones_like(X)
                    grid_num_axis = int(args.resize / args.patch)
                    max_num_patch = grid_num_axis * grid_num_axis
                    ids = [i for i in range(max_num_patch)]
                    random.shuffle(ids)
                    num_patch = int(max_num_patch * (1 - drop_rate))
                    if num_patch != 0:
                        ids = np.array(ids[:num_patch])
                        rows, cols = ids // grid_num_axis, ids % grid_num_axis
                        for r, c in zip(rows, cols):
                            add_noise_mask[:, :, r * args.patch:(r + 1) * args.patch,
                            c * args.patch:(c + 1) * args.patch] = 0
                    if args.PRM:
                        delta = delta * add_noise_mask
                    loss_kl = criterion_kl(F.log_softmax(model(X+delta), dim=1),
                                           F.softmax(model(X), dim=1))
                    grad = torch.autograd.grad(loss_kl, [delta])[0]

                    delta.data = clamp(delta + alpha * torch.sign(grad), -epsilon, epsilon)
                    delta.data = clamp(delta, lower_limit - X, upper_limit - X)
                if len(handle_list) != 0:
                    for handle in handle_list:
                        handle.remove()
                model.train()
                x_adv = Variable(X+delta, requires_grad=False)
                output = logits = model(X)
                loss_natural = F.cross_entropy(logits, y)
                loss_robust = (1.0 / batch_size) * criterion_kl(F.log_softmax(model(x_adv), dim=1),
                                                                F.softmax(model(X), dim=1))
                loss = loss_natural + beta * loss_robust
            elif args.method == 'MART':
                X = X.cuda()
                y = y.cuda()
                beta = args.beta
                kl = nn.KLDivLoss(reduction='none')
                model.eval()
                batch_size = len(X)
                epsilon = epsilon_base.cuda()
                delta = torch.zeros_like(X).cuda()
                if args.delta_init == 'random':
                    delta = 0.001 * torch.randn(X.shape).cuda()
                    delta.data = clamp(delta, lower_limit - X, upper_limit - X)
                delta.requires_grad = True
                for _ in range(args.attack_iters):
                    add_noise_mask = torch.ones_like(X)
                    grid_num_axis = int(args.resize / args.patch)
                    max_num_patch = grid_num_axis * grid_num_axis
                    ids = [i for i in range(max_num_patch)]
                    random.shuffle(ids)
                    num_patch = int(max_num_patch * (1 - drop_rate))
                    if num_patch != 0:
                        ids = np.array(ids[:num_patch])
                        rows, cols = ids // grid_num_axis, ids % grid_num_axis
                        for r, c in zip(rows, cols):
                            add_noise_mask[:, :, r * args.patch:(r + 1) * args.patch,
                            c * args.patch:(c + 1) * args.patch] = 0
                    if args.PRM:
                        delta = delta * add_noise_mask
                    output = model(X + delta)
                    loss = F.cross_entropy(output, y)
                    grad = torch.autograd.grad(loss, delta)[0].detach()
                    delta.data = clamp(delta + alpha * torch.sign(grad), -epsilon, epsilon)
                    delta.data = clamp(delta, lower_limit - X, upper_limit - X)
                delta = delta.detach()
                if len(handle_list) != 0:
                    for handle in handle_list:
                        handle.remove()
                model.train()
                x_adv = Variable(X+delta,requires_grad=False)
                logits = model(X)
                logits_adv = model(x_adv)
                adv_probs = F.softmax(logits_adv, dim=1)
                tmp1 = torch.argsort(adv_probs, dim=1)[:, -2:]
                new_y = torch.where(tmp1[:, -1] == y, tmp1[:, -2], tmp1[:, -1])
                loss_adv = F.cross_entropy(logits_adv, y) + F.nll_loss(torch.log(1.0001 - adv_probs + 1e-12), new_y)
                nat_probs = F.softmax(logits, dim=1)
                true_probs = torch.gather(nat_probs, 1, (y.unsqueeze(1)).long()).squeeze()
                loss_robust = (1.0 / batch_size) * torch.sum(
                    torch.sum(kl(torch.log(adv_probs + 1e-12), nat_probs), dim=1) * (1.0000001 - true_probs))
                loss = loss_adv + float(beta) * loss_robust
            else:
                raise ValueError(args.method)
            opt.zero_grad()
            model.zero_grad()
            (loss / args.accum_steps).backward()
            if args.method == 'AT':
                acc = (output.max(1)[1] == y.max(1)[1]).float().mean()
                if args.prompted:
                    if args.disjoint_prompts:
                        delta = simul_pgd(model, X, y, epsilon_base, alpha, args, criterion, handle_list, drop_rate,prompts=[prompt, prompt2]).detach()
                        out = (model(X + delta, prompt2) + model(X+delta, prompt))/2
                        p_acc = (out.max(1)[1] == y.max(1)[1]).float().mean().item()
                    else:
                        delta = pgd_attack(model, X, y, epsilon_base, alpha, args, criterion, handle_list, drop_rate, prompt=prompt).detach()
                        out = model(X+delta, prompt)
                        p_acc = (out.max(1)[1] == y.max(1)[1]).float().mean().item()
                    return loss, acc, y, p_acc, handle_list
            else:
                acc = (output.max(1)[1] == y.max(1)[1]).float().mean()
                if args.disjoint_prompts and args.prompted:
                    delta = pgd_attack(model, X, y, epsilon_base, alpha, args, criterion, handle_list, drop_rate, prompt=prompt).detach()
                    out = model(X+delta, [prompt2, prompt]).detach()
                    p_acc = (out.max(1)[1] == y.max(1)[1]).float().mean().item()
                    return loss, acc, y, p_acc, handle_list
            
            return loss, acc,y, acc.item(), handle_list
        if args.method in ['voting']:
            accs_ind = torch.zeros(len(prompts))
            accs_vote = torch.zeros(len(prompts))

        for step, (X, y) in enumerate(train_loader):
            batch_size = args.batch_size // args.accum_steps
            epoch_now = epoch - 1 + (step + 1) / len(train_loader)

            X_ = X[0: batch_size].cuda()  # .permute(0, 3, 1, 2)
            y_ = y[0: batch_size].cuda()  # .max(dim=-1).indices
            # X = X[y.detach() != 9].detach()
            # y = y[y.detach()!=9].detach()
            # if X.size(0) % 2 == 1:
            #     X = X[:-1].detach()
            #     y = y[:-1].detach()
            # print(X.size(), y.size())
            if len(X_) == 0:
                break
            # print(y.size())
            if args.method in ['voting']:
                accs, accs_maj, losses, acc_clean, acc_adv = train_step(X, y, epoch_now, mixup_fn, hist_a, hist_c, corr_mats)
                train_n += y_.size(0)
                train_loss += losses * y_.size(0)
                train_clean += acc_clean * y_.size(0)
                accs_ind += accs * y_.size(0)
                accs_vote += accs_maj * y_.size(0)
                train_acc += acc_adv * y_.size(0)
                def floats_to_str(floats):
                    string = "("
                    s = 0
                    n = 0
                    for f in floats:
                        s += f
                        n += 1
                        string += "{:.4f} ".format(f)
                    return string + "{:.4f})".format(s/n)
                # cosim = nn.CosineSimilarity(2)
                # things = lambda x, y: cosim(x,y).detach()
                if (step + 1) % args.log_interval == 0 or step + 1 == steps_per_epoch:
                    logger.info('Training epoch {} step {}/{}, lr {:.4f} loss {:.4f} ind acc {} clean acc {:.4f} vote acc {} adv acc {:.4f}'.format(
                        epoch, step + 1, len(train_loader),
                        opts[0].param_groups[0]['lr'],
                            losses / train_n, floats_to_str(accs_ind / train_n), train_clean/ train_n, floats_to_str([torch.norm(p).item() for p in prompts]), train_acc/train_n
                    ))
                if (step+1) % 50 == 0:
                    # fg, axarr = plt.subplots(2,1)
                    # axarr[0].bar(range(10), hist_c.cpu().numpy())
                    # axarr[0].set_title("True labels")
                    # axarr[1].bar(range(10), hist_a.cpu().numpy())
                    # axarr[1].set_title("Adv labels")
                    # path = os.path.join(args.out_dir)
                    # plt.savefig(args.out_dir + "/hist_epoch_"+str(epoch)+"step_"+str(step) + ".png")
                    # plt.clf()
                    fg, axarr = plt.subplots(len(prompts), len(prompts) + 2)
                    if len(prompts) > 1:
                        for i, c in enumerate(corr_mats):
                            for j in range(len(corr_mats) + 2):
                                axarr[i,j].matshow(c[j,:,:]/train_n)
                    else:
                        axarr[0].matshow(corr_mats[0][0,:,:]/train_n)
                        # axarr[0].axis('off')
                        axarr[0].yaxis.tick_left()
                        axarr[0].set_title('clean samples')
                        axarr[0].set_xlabel('predicted label\n' + "Acc: {:.2f}".format(train_clean/train_n * 100))
                        axarr[0].set_ylabel('ground truth label')
                        axarr[1].matshow(corr_mats[0][1,:,:]/train_n)
                        # axarr[1].axis('off')
                        axarr[1].yaxis.tick_left()
                        axarr[1].set_title('perturbed samples')
                        axarr[1].set_xlabel('predicted label\n' + "Acc: {:.2f}".format(train_prompted/train_n * 100))
                        axarr[2].yaxis.tick_left()
                        axarr[2].set_title('untargetted attacks')
                        axarr[2].set_xlabel('predicted label\n' + "Acc: {:.2f}".format(train_acc/train_n * 100))
                        axarr[2].matshow(corr_mats[0][2,:,:]/train_n)
                    plt.savefig(args.out_dir + "/mat_epoch_"+str(epoch)+"step_" + str(step) + ".png")
                    plt.close()
            else:
                # if args.method == 'ws' and args.ws == epoch:
                #     opt = torch.optim.SGD([prompt], lr=args.lr_max, momentum=args.momentum, weight_decay=args.weight_decay)
                loss, acc,y, p_acc, handle_list = train_step(X,y,epoch_now,mixup_fn, hist_a, hist_c, corr_mats)
                # print(y.max(1)[1].size())
                train_loss += loss.item() * y_.size(0)
                train_acc += acc * y_.size(0)
                def clean_acc(X, y):
                    global prompt, done_prompt
                    X = X.cuda()
                    y = y.cuda()
                    # X = X[y.size(0)]
                    if args.prompted or args.prompt_too:
                        if args.disjoint_prompts:
                            output = model(X, (prompt2 + prompt)/2)
                        else:
                            output = model(X, joint_p(prompt, done_prompt))
                    elif args.blocked:
                        output = model(X, prompt(X))
                    else:
                        output = model(X)
                    acc = (output.max(1)[1] == y.max(1)[1]).float().mean()
                    return acc
                    # print(output.shape, y.shape)
                train_prompted += p_acc * y_.size(0)
                train_clean += clean_acc(X, y).item() * y_.size(0)
                train_n += y_.size(0)
                grad_norm = torch.nn.utils.clip_grad_norm_(list(model.parameters()) + [prompt], args.grad_clip)
                # cp = prompt.clone()
                opt.step()
                # print(prompt - cp)
                opt.zero_grad()
                model.zero_grad()
                if args.prompted and args.disjoint_prompts:
                    if step < args.n_w:
                        drop_rate = step / args.n_w * args.drop_rate
                    else:
                        drop_rate = args.drop_rate
                    X = X.cuda()
                    y = y.cuda()
                    delta = pgd_attack(model, X, y, epsilon_base, alpha, args, criterion, handle_list, drop_rate, prompt=prompt).detach()
                    output = model(X+delta, prompt2)
                    loss2 = criterion(output, y)
                    loss2 += F.mse_loss(output, model(X+delta, prompt))
                    if args.mix_lam > 0:
                            out = model(X, prompt)
                            loss2 += (args.mix_lam * criterion(out, y))
                            loss2 /= (1 + args.mix_lam)
                    
                    opt.zero_grad()
                    model.zero_grad()
                    loss2.backward()
                    grad_norm = torch.nn.utils.clip_grad_norm_(list(model.parameters()) + [prompt], args.grad_clip)
                    opt.step()
                    opt.zero_grad()
                    model.zero_grad()
                if (step+1) % 50 == 0:
                #     # fg, axarr = plt.subplots(2,1)
                #     # axarr[0].bar(range(10), hist_c.cpu().numpy())
                #     # axarr[0].set_title("True labels")
                #     # axarr[1].bar(range(10), hist_a.cpu().numpy())
                #     # axarr[1].set_title("Adv labels")
                #     # path = os.path.join(args.out_dir)
                #     # plt.savefig(args.out_dir + "/hist_epoch_"+str(epoch)+"step_"+str(step) + ".png")
                #     # plt.clf()
                    fg, axarr = plt.subplots(len(prompts), len(prompts) + 2)
                    if len(prompts) > 1:
                        for i, c in enumerate(corr_mats):
                            for j in range(len(corr_mats) + 2):
                                axarr[i,j].matshow(c[j,:,:]/train_n)
                    else:
                        axarr[0].matshow(corr_mats[0][0,:,:]/train_n)
                        # axarr[0].axis('off')
                        axarr[0].yaxis.tick_left()
                        axarr[0].set_title('clean samples')
                        axarr[0].set_xlabel('predicted label\n' + "Acc: {:.2f}".format(train_clean/train_n * 100))
                        axarr[0].set_ylabel('ground truth label')
                        axarr[1].matshow(corr_mats[0][1,:,:]/train_n)
                        # axarr[1].axis('off')
                        axarr[1].yaxis.tick_left()
                        axarr[1].set_title('perturbed samples')
                        axarr[1].set_xlabel('predicted label\n' + "Acc: {:.2f}".format(train_prompted/train_n * 100))
                        axarr[2].yaxis.tick_left()
                        axarr[2].set_title('untargetted attacks')
                        axarr[2].set_xlabel('predicted label\n' + "Acc: {:.2f}".format(train_acc/train_n * 100))
                        axarr[2].matshow(corr_mats[0][2,:,:]/train_n)
                        # axarr[2].axis('off')
                    plt.savefig(args.out_dir + "/mat_epoch_"+str(epoch)+"step_" + str(step) + ".png", dpi=500)
                if (step + 1) % args.log_interval == 0 or step + 1 == steps_per_epoch:
                    logger.info('Training epoch {} step {}/{}, lr {:.4f} loss {:.4f} acc {:.4f} clean acc {:.4f} prompt atk {:.4f}'.format(
                        epoch, step + 1, len(train_loader),
                        opt.param_groups[0]['lr'],
                            train_loss / train_n, train_acc / train_n, train_clean/ train_n, train_prompted/ train_n
                    ))
                # if step % 5 == 0:
                #     break
            lr = lr_schedule(epoch_now)
            opt.param_groups[0].update(lr=lr)
            # for o in opts:
            #     o.param_groups[0].update(lr=lr)
        if epoch % 2 ==0 and args.method == 'splits':
            logger.info("Adding {:d} tokens".format(args.prompt_length))
            done_prompt = prompt if done_prompt is None else torch.cat((prompt.detach(), done_prompt.detach()), dim=1).requires_grad_()#joint_p(prompt, done_prompt)#.requires_grad_()
            # done_prompt.requires_grad = True
            prompt = make_prompt(args.prompt_length, 768)
            # prompt.requires_grad = True
            # opt.add_param_group({"params" : [prompt})
            logger.info("Total length is " + str(prompt.size(1) + done_prompt.size(1)))
            # print('sag')
            # print(prompt[:,0,-1])
            # for i in range(done_prompt.size(1)//10):
            #     print(done_prompt[:,i * 10,-1])
            opt = torch.optim.SGD([prompt, done_prompt] + list(model.module.head.parameters()), lr=lr_schedule(epoch_now), momentum=args.momentum, weight_decay=args.weight_decay) 
            
            # current_ind -= 20
            # print("to {:d}, {:d}".format(last_ind, current_ind))
        
        # if current_ind < 0:
        #     current_ind = 0
        #     last_ind = 20
        path = os.path.join(args.out_dir, 'checkpoint_{}'.format(epoch))
        if args.test:
            with open(os.path.join(args.out_dir, 'test_PGD20.txt'),'a') as new:
                args.eval_iters = 20
                args.eval_restarts = 1
                pgd_loss, pgd_acc = evaluate_pgd(args, model, test_loader)
                logger.info('test_PGD20 : loss {:.4f} acc {:.4f}'.format(pgd_loss, pgd_acc))
                new.write('{:.4f}   {:.4f}\n'.format(pgd_loss, pgd_acc))
            with open(os.path.join(args.out_dir, 'test_acc.txt'), 'a') as new:
                meter_test = evaluate_natural(args, model, test_loader, verbose=False)
                new.write('{}\n'.format(meter_test))
        if epoch == args.epochs or epoch % args.chkpnt_interval == 0:
            if args.method in ['voting']:
                torch.save({'state_dict': model.state_dict(), 'epoch': epoch, 'opts': [opt], 'prompts': prompts}, path)
            elif args.method in ['splits']:
                torch.save({'state_dict': model.state_dict(), 'epoch': epoch, 'opts': [opt], 'prompt': [prompt], 'done': [done_prompt]}, path)
            else:
                if args.prompted or args.prompt_too:
                    prompt_save = [prompt, prompt2] if args.disjoint_prompts else [prompt]
                elif args.blocked:
                    prompt_save = [prompt.state_dict()]
                else:
                    prompt_save = None
                torch.save({'state_dict': model.state_dict(), 'epoch': epoch, 'opt': opt.state_dict(), 'prompt': prompt_save}, path)
            logger.info('Checkpoint saved to {}'.format(path))


train_adv(args, model, train_loader, test_loader, logger)

args.eval_iters = 20
logger.info(args.out_dir)
print(args.out_dir)
evaluate_natural(args, model, test_loader, verbose=False, prompt=prompt)

# # cw_loss, cw_acc = evaluate_CW(args, model, test_loader, prompt=prompt)
# # logger.info('cw20 : loss {:.4f} acc {:.4f}'.format(cw_loss, cw_acc))

mats = evaluate_splits(args, model, test_loader, prompt=prompt[:,20:,:])
fg, axarr = plt.subplots(len(mats), len(mats))
for i, c in enumerate(mats):
    for j in range(len(mats)):
        axarr[i,j].matshow(c)
plt.savefig(args.out_dir + "/mat_of_splits_len{:d}.png".format(str(prompt.size(1) - 20)))
logger.info('Saved mat to outdir')

args.eval_iters = 1
args.alpha = 10
pgd_loss, pgd_acc = evaluate_pgd(args, model, test_loader, prompt=prompt)
logger.info('FGSM : loss {:.4f} acc {:.4f}'.format(pgd_loss, pgd_acc))

args.eval_iters = 10
args.alpha = 2
pgd_loss, pgd_acc = evaluate_pgd(args, model, test_loader, prompt=prompt)
logger.info('PGD10: loss {:.4f} acc {:.4f}'.format(pgd_loss, pgd_acc))




at_path = os.path.join(args.out_dir, 'result_'+'_autoattack.txt')
evaluate_aa(args, model,at_path, args.AA_batch)