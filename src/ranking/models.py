import ast
import collections
import hashlib
import os
import re
from copy import deepcopy
from pydoc import locate
from urllib.parse import urljoin

import magic
import requests
import tqdm
from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.core.management import call_command
from django.db import models, transaction
from django.db.models import F, OuterRef, Q, Sum
from django.db.models.functions import Coalesce, Upper
from django.db.models.signals import m2m_changed, post_delete, post_save, pre_save
from django.dispatch import receiver
from django.urls import reverse
from django.utils import timezone
from django.utils.crypto import get_random_string
from django_countries.fields import CountryField
from django_print_sql import print_sql
from sql_util.utils import Exists, SubqueryCount, SubquerySum

from clist.models import Contest, Resource
from clist.templatetags.extras import add_prefix_to_problem_short, get_problem_short, slug
from pyclist.indexes import ExpressionIndex, GistIndexTrgrmOps
from pyclist.models import BaseManager, BaseModel
from true_coders.models import Coder, Party

AVATAR_RELPATH_FIELD = 'avatar_relpath_'


class Account(BaseModel):
    coders = models.ManyToManyField(Coder, blank=True)
    resource = models.ForeignKey(Resource, on_delete=models.CASCADE)
    key = models.CharField(max_length=1024, null=False, blank=False)
    name = models.CharField(max_length=1024, null=True, blank=True)
    country = CountryField(null=True, blank=True, db_index=True)
    url = models.CharField(max_length=4096, null=True, blank=True)
    n_contests = models.IntegerField(default=0, db_index=True)
    n_writers = models.IntegerField(default=0, db_index=True)
    last_activity = models.DateTimeField(default=None, null=True, blank=True, db_index=True)
    last_submission = models.DateTimeField(default=None, null=True, blank=True, db_index=True)
    rating = models.IntegerField(default=None, null=True, blank=True, db_index=True)
    rating50 = models.SmallIntegerField(default=None, null=True, blank=True, db_index=True)
    resource_rank = models.IntegerField(null=True, blank=True, default=None, db_index=True)
    info = models.JSONField(default=dict, blank=True)
    updated = models.DateTimeField(auto_now_add=True)
    duplicate = models.ForeignKey('Account', null=True, blank=True, on_delete=models.CASCADE)
    global_rating = models.IntegerField(null=True, blank=True, default=None, db_index=True)
    need_verification = models.BooleanField(default=False)
    is_subscribed = models.BooleanField(null=True, blank=True, default=None, db_index=True)
    deleted = models.BooleanField(null=True, blank=True, default=None, db_index=True)

    def __str__(self):
        return '%s on %s' % (str(self.key), str(self.resource_id))

    def dict(self):
        return {
            'pk': self.pk,
            'account': self.key,
            'resource': self.resource.host,
            'name': self.name,
        }

    def dict_with_info(self):
        ret = self.dict()
        ret.update(self.info.get('profile_url', {}))
        return ret

    def avatar_url(self, resource=None):
        resource = resource or self.resource
        if resource.avatar_url:
            try:
                avatar_url_info = resource.info.get('avatar_url', {})
                fields = avatar_url_info.get('fields')
                if fields and any(not self.info.get(f) for f in fields):
                    return
                url = resource.avatar_url.format(key=self.key, info=self.info)
                if avatar_url_info.get('urljoin'):
                    url = urljoin(avatar_url_info.get('urljoin'), url)
                return url
            except KeyError:
                pass

    @staticmethod
    def is_special_info_field(field):
        if not field:
            return False
        if field[0] == '_' or field[-1] == '_' or '___' in field:
            return True
        if field in {'profile_url', 'rating', 'is_virtual'}:
            return True
        field = field.lower()
        if field in {'telegram', 'dateofbirth'}:
            return True
        if 'email' in field:
            return True

    class Meta:
        indexes = [
            GistIndexTrgrmOps(fields=['key']),
            GistIndexTrgrmOps(fields=['name']),
            GistIndexTrgrmOps(fields=['key', 'name']),
            ExpressionIndex(expressions=[Upper('key')]),
            models.Index(fields=['resource', 'key']),
            models.Index(fields=['resource', 'name']),
            models.Index(fields=['resource', 'country']),

            models.Index(fields=['resource', 'rating']),
            models.Index(fields=['resource', '-rating']),
            models.Index(fields=['resource', 'rating50']),
            models.Index(fields=['resource', '-rating50']),
            models.Index(fields=['resource', 'last_activity']),
            models.Index(fields=['resource', '-last_activity']),
            models.Index(fields=['resource', 'n_contests']),
            models.Index(fields=['resource', '-n_contests']),
            models.Index(fields=['resource', 'n_writers']),
            models.Index(fields=['resource', '-n_writers']),
            models.Index(fields=['resource', 'updated']),
            models.Index(fields=['resource', '-updated']),
            models.Index(fields=['resource', 'resource_rank']),
            models.Index(fields=['resource', '-resource_rank']),

            models.Index(fields=['resource', 'country', '-rating']),
            models.Index(fields=['resource', 'country', '-rating50']),
            models.Index(fields=['resource', 'country', '-last_activity']),
            models.Index(fields=['resource', 'country', '-n_contests']),
            models.Index(fields=['resource', 'country', '-n_writers']),
            models.Index(fields=['resource', 'country', '-updated']),
            models.Index(fields=['resource', 'country', 'resource_rank']),
            models.Index(fields=['resource', 'country', '-resource_rank']),

            models.Index(fields=['country', '-rating']),
            models.Index(fields=['country', '-rating50']),
            models.Index(fields=['country', '-last_activity']),
            models.Index(fields=['country', '-n_contests']),
            models.Index(fields=['country', '-n_writers']),
            models.Index(fields=['country', '-updated']),
            models.Index(fields=['country', 'resource_rank']),
            models.Index(fields=['country', '-resource_rank']),
        ]

        unique_together = ('resource', 'key')

    def get_last_season(self):
        if not self.last_activity:
            return
        date = self.last_activity
        year = date.year - (0 if date.month > 8 else 1)
        season = f'{year}-{year + 1}'
        return season


class AccountRenaming(BaseModel):
    resource = models.ForeignKey(Resource, on_delete=models.CASCADE)
    old_key = models.CharField(max_length=1024, null=False, blank=False)
    new_key = models.CharField(max_length=1024, null=True, blank=True)

    def __str__(self):
        return f'{self.old_key} -> {self.new_key}'

    class Meta:
        indexes = [
            GistIndexTrgrmOps(fields=['old_key']),
            GistIndexTrgrmOps(fields=['new_key']),
            models.Index(fields=['resource', 'old_key']),
            models.Index(fields=['resource', 'new_key']),
        ]

        unique_together = ('resource', 'old_key')


def download_avatar_url(account):
    download_avatar_url = account.info.pop('download_avatar_url_', None)
    if not download_avatar_url:
        return

    checksum_field = account.resource.info.get('standings', {}).get('download_avatar_checksum_field')
    if checksum_field:
        headers = requests.head(download_avatar_url).headers
        checksum_value = headers[checksum_field]
        checksum_field += '_'
        if checksum_value == account.info.get(checksum_field):
            return

    response = requests.get(download_avatar_url)
    if response.status_code != 200:
        return

    content_type = response.headers.get('Content-Type')
    if not content_type:
        content_type = magic.from_buffer(response.content, mime=True)
    ext = content_type.split('/')[-1]
    folder = re.sub('[./]', '_', account.resource.host)
    hashname = hashlib.md5(download_avatar_url.encode()).hexdigest()
    hashname = hashname[:2] + '/' + hashname[2:4] + '/' + hashname[4:]
    relpath = os.path.join('avatars', folder, f'{hashname}.{ext}')
    filepath = os.path.join(settings.MEDIA_ROOT, relpath)

    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    with open(filepath, 'wb') as fo:
        fo.write(response.content)

    account.info[AVATAR_RELPATH_FIELD] = relpath
    if checksum_field:
        account.info[checksum_field] = checksum_value


@receiver(pre_save, sender=Account)
def set_account_rating(sender, instance, *args, **kwargs):
    if instance.deleted:
        instance.rating = None
        instance.rating50 = None
        instance.resource_rank = None
    elif 'rating' in instance.info:
        if instance.rating != instance.info['rating']:
            instance.resource.rating_update_time = timezone.now()
            instance.resource.save(update_fields=['rating_update_time'])
        instance.rating = instance.info['rating']
        instance.rating50 = instance.rating / 50 if instance.rating is not None else None
        if instance.rating is None:
            instance.resource_rank = None
    download_avatar_url(instance)


@receiver(post_save, sender=Account)
@receiver(post_delete, sender=Account)
def count_resource_accounts(signal, instance, **kwargs):
    if signal is post_delete:
        instance.resource.n_accounts -= 1
        instance.resource.save(update_fields=['n_accounts'])
    elif signal is post_save and kwargs['created']:
        instance.resource.n_accounts += 1
        instance.resource.save(update_fields=['n_accounts'])


def update_account_by_coders(account, default_url=None):
    url = False
    custom_countries = None
    for idx, coder in enumerate(account.coders.iterator()):
        if url:
            url = True
        else:
            url = reverse('coder:profile', args=[coder.username]) + f'?search=resource:{account.resource.host}'

        if custom_countries is None:
            custom_countries = coder.settings.get('custom_countries', {})
        else:
            d = {}
            for k, v in coder.settings.get('custom_countries', {}).items():
                if custom_countries.get(k) == v:
                    d[k] = v
            custom_countries = d

            if idx >= 1 and not custom_countries:
                break

    if isinstance(url, bool):
        url = default_url

    if url:
        account.url = url

    account.info['custom_countries_'] = custom_countries or {}

    account.save()


@receiver(pre_save, sender=Account)
@receiver(m2m_changed, sender=Account.coders.through)
def update_account_url(signal, instance, **kwargs):
    if kwargs.get('reverse'):
        return
    account = instance

    def default_url():
        args = [account.key, account.resource.host]
        return reverse('coder:account', args=args)

    if signal is pre_save:
        if account.url:
            return
        account.url = default_url()
    elif signal is m2m_changed:
        if not kwargs.get('action').startswith('post_'):
            return
        update_account_by_coders(account, default_url=default_url())


@receiver(m2m_changed, sender=Account.writer_set.through)
def update_account_writer(signal, instance, action, reverse, pk_set, **kwargs):
    when, action = action.split('_', 1)
    if when != 'post':
        return
    if action == 'add':
        delta = 1
    elif action == 'remove':
        delta = -1
    else:
        return

    if reverse:
        instance.n_writers += delta
        instance.save()
    else:
        Account.objects.filter(pk__in=pk_set).update(n_writers=F('n_writers') + delta)


@receiver(m2m_changed, sender=Account.coders.through)
def update_coder_n_accounts_and_n_contests(signal, instance, action, reverse, pk_set, **kwargs):
    if action not in ['post_add', 'post_remove']:
        return

    if reverse:
        instance.n_accounts = instance.account_set.count()
        instance.n_contests = instance.account_set.aggregate(total=Sum('n_contests'))['total'] or 0
        instance.save()

        resources = Resource.objects.annotate(has_account=Exists('account', filter=Q(pk__in=pk_set)))
        resources = resources.filter(has_account=True)
        resources = list(resources.values_list('host', flat=True))
        coders = [instance.username]
    else:
        Coder.objects.filter(pk__in=pk_set) \
            .annotate(n_a=SubqueryCount('account')) \
            .annotate(n_c=SubquerySum('account__n_contests')) \
            .update(n_accounts=F('n_a'), n_contests=Coalesce('n_c', 0))

        coders = list(Coder.objects.filter(pk__in=pk_set).values_list('username', flat=True))
        resources = [instance.resource.host]

    if coders and resources:
        call_command('fill_coder_problems', coders=coders, resources=resources)


class AccountVerification(BaseModel):
    coder = models.ForeignKey(Coder, on_delete=models.CASCADE)
    account = models.ForeignKey(Account, on_delete=models.CASCADE)
    code = models.CharField(max_length=10)

    class Meta:
        unique_together = ('coder', 'account')

    def text(self):
        return f'ClistCheckCode{self.code}'


class VerifiedAccount(BaseModel):
    coder = models.ForeignKey(Coder, on_delete=models.CASCADE, related_name='verified_accounts')
    account = models.ForeignKey(Account, on_delete=models.CASCADE, related_name='verified_accounts')

    class Meta:
        unique_together = ('coder', 'account')


@receiver(pre_save, sender=AccountVerification)
def account_verification_pre_save(sender, instance, *args, **kwargs):
    if not instance.code:
        instance.code = get_random_string(length=10)


class Rating(BaseModel):
    contest = models.ForeignKey(Contest, on_delete=models.CASCADE)
    party = models.ForeignKey(Party, on_delete=models.CASCADE)

    def __str__(self):
        return 'rating %s by %s' % (str(self.party.name), str(self.contest.title))

    class Meta:
        unique_together = ('contest', 'party')


class AutoRating(BaseModel):
    party = models.ForeignKey(Party, on_delete=models.CASCADE)
    info = models.JSONField(default=dict, blank=True)
    deadline = models.DateTimeField()

    def __str__(self):
        return 'auto rating [%d] with party [%d]' % (self.pk, self.party_id)


class Statistics(BaseModel):
    account = models.ForeignKey(Account, on_delete=models.CASCADE)
    contest = models.ForeignKey(Contest, on_delete=models.CASCADE)
    place = models.CharField(max_length=17, default=None, null=True, blank=True)
    place_as_int = models.IntegerField(default=None, null=True, blank=True)
    solving = models.FloatField(default=0, blank=True)
    upsolving = models.FloatField(default=0, blank=True)
    addition = models.JSONField(default=dict, blank=True)
    url = models.TextField(null=True, blank=True)
    new_global_rating = models.IntegerField(null=True, blank=True, default=None, db_index=True)
    global_rating_change = models.IntegerField(null=True, blank=True, default=None)
    skip_in_stats = models.BooleanField(default=False)
    advanced = models.BooleanField(default=False)
    last_activity = models.DateTimeField(default=None, null=True, blank=True, db_index=True)
    rating_prediction = models.JSONField(default=None, null=True, blank=True)

    @staticmethod
    def is_special_addition_field(field):
        if not field:
            return False
        if field[0] == '_' or field[-1] == '_':
            return True
        return field in settings.ADDITION_HIDE_FIELDS_

    def __str__(self):
        return f'{self.account_id} on {self.contest_id} = {self.solving} + {self.upsolving}'

    def get_old_rating(self, use_rating_prediction=True):
        rating_datas = [self.addition]
        if use_rating_prediction:
            rating_datas.append(self.rating_prediction)
        for rating_data in rating_datas:
            if not rating_data:
                continue
            if 'old_rating' in rating_data:
                return rating_data['old_rating']
            if 'new_rating' in rating_data and 'rating_change' in rating_data:
                return rating_data['new_rating'] - rating_data['rating_change']

    class Meta:
        verbose_name_plural = 'Statistics'
        unique_together = ('account', 'contest')

        indexes = [
            models.Index(fields=['place_as_int', 'created']),
            models.Index(fields=['place_as_int', '-solving']),
            models.Index(fields=['place_as_int', '-created']),
            models.Index(fields=['contest', 'place_as_int', '-solving', 'id']),
            models.Index(fields=['contest', 'account']),
            models.Index(fields=['contest', 'advanced', 'place_as_int']),
            models.Index(fields=['contest', 'account', 'advanced', 'place_as_int']),
            models.Index(fields=['account', 'advanced']),
            models.Index(fields=['account', 'skip_in_stats']),
        ]


@receiver(post_save, sender=Statistics)
@receiver(post_delete, sender=Statistics)
def count_account_contests(signal, instance, **kwargs):
    if instance.skip_in_stats:
        return

    if signal is post_delete:
        instance.account.n_contests -= 1
        instance.account.save(update_fields=['n_contests'])
    elif signal is post_save:
        if kwargs['created']:
            instance.account.n_contests += 1
            instance.account.save(update_fields=['n_contests'])

        if (
            instance.last_activity and
            (not instance.account.last_activity or instance.account.last_activity < instance.last_activity)
        ):
            instance.account.last_activity = instance.last_activity
            instance.account.save(update_fields=['last_activity'])


class Module(BaseModel):
    resource = models.OneToOneField(Resource, on_delete=models.CASCADE)
    path = models.CharField(max_length=255)
    enable = models.BooleanField(default=True, blank=True, db_index=True)
    min_delay_after_end = models.DurationField(null=True, blank=True)
    max_delay_after_end = models.DurationField()
    delay_on_error = models.DurationField()
    delay_on_success = models.DurationField(null=True, blank=True)
    long_contest_idle = models.DurationField(default='06:00:00', blank=True)
    long_contest_divider = models.IntegerField(default=15)

    class BaseModuleManager(BaseManager):
        def get_queryset(self):
            return super().get_queryset().select_related('resource')

    objects = BaseModuleManager()

    def __str__(self):
        return f'{self.resource.host} Module#{self.id}'


class Stage(BaseModel):
    contest = models.OneToOneField(Contest, on_delete=models.CASCADE)
    filter_params = models.JSONField(default=dict, blank=True)
    score_params = models.JSONField(default=dict, blank=True)
    contests = models.ManyToManyField(Contest, related_name='stages', blank=True)

    def __str__(self):
        return 'Stage#%d %s' % (self.pk, self.contest)

    def update(self):
        eps = 1e-9
        stage = self.contest

        filter_params = dict(self.filter_params)
        spec_filter_params = dict()
        for field in ('info__fields_types__new_rating__isnull',):
            if field in filter_params:
                spec_filter_params[field] = filter_params.pop(field)
        is_over = self.contest.end_time < timezone.now()

        contests = Contest.objects.filter(
            resource=self.contest.resource,
            start_time__gte=self.contest.start_time,
            end_time__lte=self.contest.end_time,
            **filter_params,
        ).exclude(pk=self.contest.pk)

        if spec_filter_params:
            contests = contests.filter(Q(**spec_filter_params) | Q(end_time__gt=timezone.now()))

        contests = contests.order_by('start_time')
        contests = contests.prefetch_related('writers')
        self.contests.set(contests)

        parsed_statistic = self.score_params.get('parse_statistic')
        if parsed_statistic:
            call_command('parse_statistic',
                         contest_id=stage.pk,
                         without_fill_coder_problems=True,
                         ignore_stage=True)
            stage.refresh_from_db()

        placing = self.score_params.get('place')
        n_best = self.score_params.get('n_best')
        fields = self.score_params.get('fields', [])
        scoring = self.score_params.get('scoring', {})
        detail_problems = self.score_params.get('detail_problems')
        order_by = self.score_params['order_by']
        advances = self.score_params.get('advances', {})
        results = collections.defaultdict(collections.OrderedDict)

        mapping_account_by_coder = {}
        fixed_fields = []
        hidden_fields = []

        problems_infos = collections.OrderedDict()
        divisions_order = []
        for idx, contest in enumerate(tqdm.tqdm(contests, desc=f'getting contests for stage {stage}'), start=1):
            info = {
                'code': str(contest.pk),
                'name': contest.title,
                'url': reverse(
                    'ranking:standings',
                    kwargs={'title_slug': slug(contest.title), 'contest_id': str(contest.pk)}
                ),
            }

            if contest.end_time > timezone.now():
                info['subtext'] = {'text': 'upcoming', 'title': str(contest.end_time)}

            for division in contest.info.get('divisions_order', []):
                if division not in divisions_order:
                    divisions_order.append(division)

            if self.score_params.get('regex_problem_name'):
                match = re.search(self.score_params.get('regex_problem_name'), contest.title)
                if match:
                    info['short'] = match.group(1)
            if self.score_params.get('abbreviation_problem_name'):
                info['short'] = ''.join(re.findall(r'(\b[A-Z]|[0-9])', info.get('short', contest.title)))

            problems = contest.info.get('problems', [])
            if not detail_problems:
                full_score = None
                if placing:
                    if 'division' in placing:
                        full_score = max([max(p.values()) for p in placing['division'].values()])
                    else:
                        full_score = max(placing.values())
                elif 'division' in problems:
                    full_scores = []
                    for ps in problems['division'].values():
                        full = 0
                        for problem in ps:
                            full += problem.get('full_score', 1)
                        full_scores.append(full)
                    info['full_score'] = max(full_scores)
                elif self.score_params.get('default_problem_full_score'):
                    full_score = self.score_params['default_problem_full_score']
                else:
                    full_score = 0
                    for problem in problems:
                        full_score += problem.get('full_score', 1)
                if full_score is not None:
                    info['full_score'] = full_score
                problems_infos[str(contest.pk)] = info
            else:
                for problem in problems:
                    problem = dict(problem)
                    add_prefix_to_problem_short(problem, f'{idx}.')
                    problem['group'] = info.get('short', info['name'])
                    problem['url'] = info['url']
                    problems_infos[get_problem_short(problem)] = problem

        exclude_advances = {}
        if advances and advances.get('exclude_stages'):
            qs = Statistics.objects \
                .filter(contest__stage__in=advances['exclude_stages'], addition___advance__isnull=False) \
                .values('account__key', 'addition___advance', 'contest__title') \
                .order_by('contest__end_time', 'contest__id')
            for r in qs:
                d = r['addition___advance']
                if 'contest' not in d:
                    d['contest'] = r['contest__title']
                exclude_advances[r['account__key']] = d

        statistics = Statistics.objects \
            .select_related('account', 'account__duplicate') \
            .prefetch_related('account__coders')
        filter_statistics = self.score_params.get('filter_statistics')
        if filter_statistics:
            statistics = statistics.filter(**filter_statistics)

        def get_placing(placing, stat):
            return placing['division'][stat.addition['division']] if 'division' in placing else placing

        account_keys = dict()
        total = statistics.filter(contest__in=contests).count()
        with tqdm.tqdm(total=total, desc=f'getting statistics for stage {stage}') as pbar, print_sql(count_only=True):
            for idx, contest in enumerate(contests, start=1):
                skip_problem_stat = '_skip_for_problem_stat' in contest.info.get('fields', [])
                contest_unrated = contest.info.get('unrated')

                if not detail_problems:
                    problem_info_key = str(contest.pk)
                    problem_short = get_problem_short(problems_infos[problem_info_key])
                pbar.set_postfix(contest=contest)
                stats = statistics.filter(contest_id=contest.pk)

                if placing:
                    placing_scores = deepcopy(placing)
                    n_rows = 0
                    for s in stats:
                        n_rows += 1
                        placing_ = get_placing(placing_scores, s)
                        key = str(s.place_as_int)
                        if key in placing_:
                            placing_.setdefault('scores', {})
                            placing_['scores'][key] = placing_.pop(key)
                    scores = []
                    for place in reversed(range(1, n_rows + 1)):
                        placing_ = get_placing(placing_scores, s)
                        key = str(place)
                        if key in placing_:
                            scores.append(placing_.pop(key))
                        else:
                            if scores:
                                placing_['scores'][key] += sum(scores)
                                placing_['scores'][key] /= len(scores) + 1
                            scores = []

                max_solving = 0
                n_effective = 0
                for stat in stats:
                    max_solving = max(max_solving, stat.solving)
                    n_effective += stat.solving > eps

                for s in stats:
                    if not detail_problems and not skip_problem_stat:
                        problems_infos[problem_info_key].setdefault('n_total', 0)
                        problems_infos[problem_info_key]['n_total'] += 1

                    score = None
                    if s.solving < eps:
                        score = 0
                        if placing:
                            placing_ = get_placing(placing_scores, s)
                            score = placing_.get('zero', 0)
                    else:
                        if placing:
                            placing_ = get_placing(placing_scores, s)
                            score = placing_['scores'].get(str(s.place_as_int), placing_.get('default'))
                            if score is None:
                                continue
                    if scoring:
                        if score is None:
                            score = 0
                        if scoring['name'] == 'general':
                            if s.place_as_int is None:
                                continue
                            solving_factor = s.solving / max_solving
                            rank_factor = (n_effective - s.place_as_int + 1) / n_effective
                            score += scoring['factor'] * solving_factor * rank_factor
                        elif scoring['name'] == 'field':
                            score += s.addition.get(scoring['field'], 0)
                        else:
                            raise NotImplementedError(f'scoring {scoring["name"]} is not implemented')
                    if score is None:
                        score = s.solving

                    if not detail_problems and not skip_problem_stat:
                        problems_infos[problem_info_key].setdefault('n_teams', 0)
                        problems_infos[problem_info_key]['n_teams'] += 1
                        if score:
                            problems_infos[problem_info_key].setdefault('n_accepted', 0)
                            problems_infos[problem_info_key]['n_accepted'] += 1

                    account = s.account
                    if account.duplicate is not None:
                        account = account.duplicate

                    coders = account.coders.all()
                    has_mapping_account_by_coder = False
                    if len(coders) == 1:
                        coder = coders[0]
                        if coder not in mapping_account_by_coder:
                            mapping_account_by_coder[coder] = account
                        else:
                            account = mapping_account_by_coder[coder]
                            has_mapping_account_by_coder = True

                    row = results[account]
                    row['member'] = account
                    account_keys[account.key] = account

                    problems = row.setdefault('problems', {})
                    if detail_problems:
                        for key, problem in s.addition.get('problems', {}).items():
                            p = problems.setdefault(f'{idx}.' + key, {})
                            if contest_unrated:
                                p = p.setdefault('upsolving', {})
                            p.update(problem)
                    else:
                        problem = problems.setdefault(problem_short, {})
                        if contest_unrated:
                            problem = problem.setdefault('upsolving', {})
                        problem['result'] = score
                        url = s.addition.get('url')
                        if not url:
                            url = reverse('ranking:standings_by_id', kwargs={'contest_id': str(contest.pk)})
                            url += f'?find_me={s.pk}'
                        problem['url'] = url
                    if contest_unrated:
                        score = 0

                    if n_best and not detail_problems:
                        row.setdefault('scores', []).append((score, problem))
                    else:
                        row['score'] = row.get('score', 0) + score

                    field_values = {}
                    for field in fields:
                        if field.get('skip_on_mapping_account_by_coder') and has_mapping_account_by_coder:
                            continue
                        if 'type' in field:
                            continue
                        inp = field['field']
                        out = field.get('out', inp)
                        if field.get('first') and out in row or (inp not in s.addition and not hasattr(s, inp)):
                            continue
                        val = s.addition[inp] if inp in s.addition else getattr(s, inp)
                        if not field.get('safe') and isinstance(val, str):
                            val = ast.literal_eval(val)
                        if 'cast' in field:
                            val = locate(field['cast'])(val)
                        field_values[out] = val
                        if field.get('skip'):
                            continue
                        if field.get('accumulate'):
                            val = round(val + ast.literal_eval(str(row.get(out, 0))), 2)
                        if field.get('aggregate') == 'avg':
                            out_n = f'_{out}_n'
                            out_s = f'_{out}_s'
                            row[out_n] = row.get(out_n, 0) + 1
                            row[out_s] = row.get(out_s, 0) + val
                            val = round(row[out_s] / row[out_n], 2)
                        row[out] = val

                    if 'solved' in s.addition and isinstance(s.addition['solved'], dict):
                        solved = row.setdefault('solved', {})
                        for k, v in s.addition['solved'].items():
                            solved[k] = solved.get(k, 0) + v

                    if 'status' in self.score_params:
                        field = self.score_params['status']
                        val = field_values.get(field, row.get(field))
                        if val is None:
                            val = getattr(s, field)
                        if val is not None:
                            problem['status'] = val
                    else:
                        for field in order_by:
                            field = field.lstrip('-')
                            if field in ['score', 'rating']:
                                continue
                            status = field_values.get(field, row.get(field))
                            if status is None:
                                continue
                            problem['status'] = status
                            break
                    pbar.update()

                for writer in contest.writers.all():
                    account_keys[writer.key] = writer

        total = sum([len(contest.info.get('writers', [])) for contest in contests])
        with tqdm.tqdm(total=total, desc=f'getting writers for stage {stage}') as pbar, print_sql(count_only=True):
            writers = set()
            for contest in contests:
                contest_writers = contest.info.get('writers', [])
                if not contest_writers or detail_problems:
                    continue
                problem_info_key = str(contest.pk)
                problem_short = get_problem_short(problems_infos[problem_info_key])
                for writer in contest_writers:
                    if writer in account_keys:
                        account = account_keys[writer]
                    else:
                        try:
                            account = Account.objects.get(resource_id=contest.resource_id, key__iexact=writer)
                        except Account.DoesNotExist:
                            account = None

                    pbar.update()
                    if not account:
                        continue
                    writers.add(account)

                    row = results[account]
                    row['member'] = account
                    row.setdefault('score', 0)
                    if n_best:
                        row.setdefault('scores', [])
                    row.setdefault('writer', 0)

                    row['writer'] += 1

                    problems = row.setdefault('problems', {})
                    problem = problems.setdefault(problem_short, {})
                    problem['status'] = 'W'

        if self.score_params.get('writers_proportionally_score'):
            n_contests = len(contests)
            for account in writers:
                row = results[account]
                if n_contests == row['writer'] or 'score' not in row:
                    continue
                row['score'] = row['score'] / (n_contests - row['writer']) * n_contests
        if self.score_params.get('exponential_score_decay'):
            for r in results.values():
                scores = [problem.get('result', 0) for problem in r.get('problems', {}).values()]
                scores.sort(reverse=True)
                k = self.score_params['exponential_score_decay']
                score = k * sum((1 - k) ** i * score for i, score in enumerate(scores))
                r['score'] = score

        for field in fields:
            t = field.get('type')
            if t is None:
                continue
            if t == 'points_for_common_problems':
                group = field['group']
                inp = field['field']
                out = field.get('out', inp)

                excluding = bool(exclude_advances and field.get('exclude_advances'))

                groups = collections.defaultdict(list)
                for row in results.values():
                    key = row[group]
                    groups[key].append(row)

                advancement_position = 1
                for key, rows in sorted(groups.items(), key=lambda kv: kv[0], reverse=True):
                    common_problems = None
                    for row in rows:
                        handle = row['member'].key
                        if excluding and handle in exclude_advances:
                            exclude_advance = exclude_advances[handle]
                            for advance in advances.get('options', []):
                                if advance['next'] == exclude_advance['next']:
                                    exclude_advance['skip'] = True
                                    break
                                if advancement_position in advance['places']:
                                    break
                            if exclude_advance.get('skip'):
                                continue

                        problems = {k for k, p in row['problems'].items() if p.get('status') != 'W'}
                        common_problems = problems if common_problems is None else (problems & common_problems)
                    if common_problems is None:
                        for row in rows:
                            problems = {k for k, p in row['problems'].items() if p.get('status') != 'W'}
                            common_problems = problems if common_problems is None else (problems & common_problems)

                    for row in rows:
                        handle = row['member'].key
                        if excluding and not exclude_advances.get(handle, {}).get('skip', False):
                            advancement_position += 1
                        value = 0
                        for k in common_problems:
                            value += float(row['problems'].get(k, {}).get(inp, 0))
                        for k, v in row['problems'].items():
                            if k not in common_problems and v.get('status') != 'W':
                                v['status_tag'] = 'strike'
                        row[out] = round(value, 2)
            elif t == 'region_by_country':
                out = field['out']

                mapping_regions = dict()
                for regional_event in field['data']:
                    for region in regional_event['regions']:
                        mapping_regions[region['code']] = {'regional_event': regional_event, 'region': region}

                for row in results.values():
                    country = row['member'].country
                    if not country:
                        continue
                    code = country.code
                    if code not in mapping_regions:
                        continue
                    row[out] = mapping_regions[code]['regional_event']['name']
            elif t == 'n_medal_problems':
                for row in results.values():
                    for problem in row['problems'].values():
                        medal = problem.get('medal')
                        if medal:
                            k = f'n_{medal}_problems'
                            row.setdefault(k, 0)
                            row[k] += 1
                            if k not in hidden_fields:
                                hidden_fields.append(k)
                for field in settings.STANDINGS_FIELDS_:
                    if field in hidden_fields:
                        fixed_fields.append(field)
                        hidden_fields.remove(field)
            else:
                raise ValueError(f'Unknown field type = {t}')

        hidden_fields += [field.get('out', field.get('inp')) for field in fields if field.get('hidden')]

        results = list(results.values())
        if n_best:
            for row in results:
                scores = row.pop('scores')
                for index, (score, problem) in enumerate(sorted(scores, key=lambda s: s[0], reverse=True)):
                    if index < n_best:
                        row['score'] = row.get('score', 0) + score
                    else:
                        problem['status'] = problem.pop('result')

        filtered_results = []
        filter_zero_points = self.score_params.get('filter_zero_points', True)
        for r in results:
            if r['score'] > eps or not filter_zero_points or r.get('writer'):
                filtered_results.append(r)
                continue
            if detail_problems:
                continue

            problems = r.setdefault('problems', {})

            for idx, contest in enumerate(contests, start=1):
                skip_problem_stat = '_skip_for_problem_stat' in contest.info.get('fields', [])
                if skip_problem_stat:
                    continue

                problem_info_key = str(contest.pk)
                problem_short = get_problem_short(problems_infos[problem_info_key])

                if problem_short in problems:
                    problems_infos[problem_info_key].setdefault('n_teams', 0)
                    problems_infos[problem_info_key]['n_teams'] -= 1
        results = filtered_results

        results = sorted(
            results,
            key=lambda r: tuple(r.get(k.lstrip('-'), 0) * (-1 if k.startswith('-') else 1) for k in order_by),
            reverse=True,
        )

        additions = deepcopy(stage.info.get('additions', {}))
        field_to_problem = self.score_params.get('field_to_problem')
        with transaction.atomic():
            fields_set = set()
            fields = list()

            pks = set()
            placing_infos = {}
            score_advance = None
            place_advance = 0
            place_index = 0
            for row in tqdm.tqdm(results, desc=f'update statistics for stage {stage}'):
                for field in [row.get('member'), row.get('name')]:
                    row.update(additions.pop(field, {}))

                division = row.get('division', 'none')
                placing_info = placing_infos.setdefault(division, {})
                placing_info['index'] = placing_info.get('index', 0) + 1

                curr_score = tuple(row.get(k.lstrip('-'), 0) for k in order_by)
                if curr_score != placing_info.get('last_score'):
                    placing_info['last_score'] = curr_score
                    placing_info['place'] = placing_info['index']

                if advances and ('divisions' not in advances or division in advances['divisions']):
                    tmp = score_advance, place_advance, place_index

                    place_index += 1
                    if curr_score != score_advance:
                        score_advance = curr_score
                        place_advance = place_index

                    for advance in advances.get('options', []):
                        handle = row['member'].key
                        if handle in exclude_advances and advance['next'] == exclude_advances[handle]['next']:
                            advance = exclude_advances[handle]
                            if 'class' in advance and not advance['class'].startswith('text-'):
                                advance['class'] = f'text-{advance["class"]}'
                            row['_advance'] = advance
                            break

                        if 'places' in advance and place_advance in advance['places']:
                            if not advances.get('inplace_fields_only'):
                                row['_advance'] = advance
                            if is_over:
                                for field in advance.get('inplace_fields', []):
                                    row[field] = advance[field]
                            tmp = None
                            break

                    if tmp is not None:
                        score_advance, place_advance, place_index = tmp
                account = row.pop('member')
                solving = row.pop('score')

                advanced = False
                if row.get('_advance'):
                    adv = row['_advance']
                    advanced = not adv.get('skip') and not adv.get('class', '').startswith('text-')

                defaults = {
                    'place': str(placing_info['place']),
                    'place_as_int': placing_info['place'],
                    'solving': solving,
                    'addition': row,
                    'skip_in_stats': True,
                    'advanced': advanced,
                }
                if parsed_statistic:
                    defaults['place'] = None
                    defaults['place_as_int'] = None
                    defaults['solving'] = 0
                    stat = Statistics.objects.filter(account=account, contest=stage).first()
                    if not stat:
                        continue
                    for k, v in defaults['addition'].items():
                        if k not in stat.addition or (v and not stat.addition.get(k)):
                            stat.addition[k] = v
                    stat.skip_in_stats = defaults['skip_in_stats']
                    stat.advanced = defaults['advanced']
                    stat.save(update_fields=['addition', 'skip_in_stats', 'advanced'])
                else:
                    stat, created = Statistics.objects.update_or_create(
                        account=account,
                        contest=stage,
                        defaults=defaults,
                    )
                pks.add(stat.pk)

                for k in stat.addition.keys():
                    if field_to_problem and re.search(field_to_problem['regex'], k):
                        continue
                    if k not in fields_set:
                        fields_set.add(k)
                        fields.append(k)
            stage.info['problems'] = list(problems_infos.values())

            if field_to_problem:
                for stat in stage.statistics_set.all():
                    problems = stat.addition.setdefault('problems', {})
                    was_updated = False
                    for k, v in list(stat.addition.items()):
                        match = re.search(field_to_problem['regex'], k)
                        if match:
                            problem_short = field_to_problem['format'].format(**match.groupdict())
                            if problem_short not in problems:
                                problems[problem_short] = {'result': v}
                            stat.addition.pop(k)
                            if 'fields_types' in stage.info:
                                stage.info['fields_types'].pop(k, None)
                            was_updated = True
                    if was_updated:
                        stat.save(update_fields=['addition'])

            if parsed_statistic:
                for field in stage.info.setdefault('hidden_fields', []):
                    if field not in hidden_fields:
                        hidden_fields.append(field)
            regex_hidden_fields = self.score_params.get('regex_hidden_fields')
            if regex_hidden_fields:
                for field in fields:
                    if field not in hidden_fields and re.search(regex_hidden_fields, field):
                        hidden_fields.append(field)
            stage.info['fields'] = list(fields)
            stage.info['hidden_fields'] = hidden_fields

            if not parsed_statistic:
                stage.statistics_set.exclude(pk__in=pks).delete()
                stage.n_statistics = len(results)
                stage.parsed_time = timezone.now()

                standings_info = self.score_params.get('info', {})
                standings_info['fixed_fields'] = fixed_fields + [(f.lstrip('-'), f.lstrip('-')) for f in order_by]
                stage.info['standings'] = standings_info

                if divisions_order and self.score_params.get('divisions_ordering'):
                    stage.info['divisions_order'] = divisions_order

                if stage.is_rated is None:
                    stage.is_rated = False
            stage.save()


class VirtualStart(BaseModel):
    coder = models.ForeignKey(Coder, on_delete=models.CASCADE, related_name='virtual_starts')

    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    object_id = models.PositiveIntegerField()
    entity = GenericForeignKey('content_type', 'object_id')

    start_time = models.DateTimeField()

    class Meta:
        unique_together = ('coder', 'content_type', 'object_id')
        indexes = [models.Index(fields=['coder', 'content_type', 'object_id'])]

    @classmethod
    def filter_by_content_type(cls, model_class):
        content_type = ContentType.objects.get_for_model(model_class)
        return cls.objects.filter(content_type=content_type)

    @staticmethod
    def contests_filter(coder):
        has_virtual_start = VirtualStart.filter_by_content_type(Contest).filter(coder=coder, object_id=OuterRef('id'))
        has_verdict = coder.verdicts.filter(problem__contests=OuterRef('pk'))
        return Exists(has_virtual_start) | Exists(has_verdict)
