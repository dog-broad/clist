# Generated by Django 3.1.14 on 2023-05-20 21:52

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('clist', '0103_remove_null_for_problems_times'),
        ('true_coders', '0052_coderproblem'),
    ]

    operations = [
        migrations.AlterField(
            model_name='coderproblem',
            name='verdict',
            field=models.CharField(choices=[('AC', 'Solved'), ('WA', 'Reject'), ('HT', 'Hidden'), ('PS', 'Partial'), ('??', 'Unknown')], db_index=True, max_length=2),
        ),
        migrations.AlterUniqueTogether(
            name='coderproblem',
            unique_together={('coder', 'problem')},
        ),
        migrations.AddIndex(
            model_name='coderproblem',
            index=models.Index(fields=['coder', 'verdict'], name='true_coders_coder_i_ec68b0_idx'),
        ),
        migrations.AddIndex(
            model_name='coderproblem',
            index=models.Index(fields=['problem', 'verdict'], name='true_coders_problem_787da0_idx'),
        ),
    ]