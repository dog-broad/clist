# Generated by Django 3.1.14 on 2022-12-26 14:57

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('clist', '0090_delete_timingcontest'),
    ]

    operations = [
        migrations.RemoveIndex(
            model_name='contest',
            name='clist_conte_resourc_7cc518_idx',
        ),
        migrations.AddIndex(
            model_name='contest',
            index=models.Index(fields=['resource', 'notification_timing', 'start_time', 'end_time'], name='clist_conte_resourc_ddd3b3_idx'),
        ),
        migrations.AddIndex(
            model_name='contest',
            index=models.Index(fields=['resource', 'statistic_timing', 'start_time', 'end_time'], name='clist_conte_resourc_a0dc67_idx'),
        ),
    ]