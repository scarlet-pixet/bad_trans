class PrincipalClient:

    def __init__(self, actor):
        self.actor = actor

    def get_current_user(self):
        return self.actor
