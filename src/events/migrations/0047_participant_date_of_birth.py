# Generated by Django 2.2.10 on 2020-04-16 20:44

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('events', '0046_participant_addition_fields'),
    ]

    operations = [
        migrations.AddField(
            model_name='participant',
            name='date_of_birth',
            field=models.DateField(blank=True, null=True),
        ),
    ]